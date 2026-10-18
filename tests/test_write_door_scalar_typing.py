"""Every write door answers a wrong-typed ``name``/``title`` the same way (#3001, #456).

``test_request_validation.py`` proves what :mod:`personalclaw.request_validation` DOES.
This file proves every write door actually asks it — which is the half that was missing,
and the reason two issues stayed open after the helper landed: the helper existed and a
door-by-door sweep adopted it at the doors two issue reports happened to enumerate,
leaving the identical coercion at the doors nobody had measured. ``PATCH /api/chat/tags``
sat twenty lines below its own validated POST door and still renamed a tag to the Python
repr ``{'a': 'b'}`` at 200.

**Two rails, because an enumerated rail cannot see the door it forgot.**

:data:`_DOORS` is the enumerated half — each row pins one door to the shared function it
must call, so a door that quietly reverts to ``str(body["name"])`` goes red by name.
:func:`test_no_unguarded_name_or_title_coercion_survives` is the half that covers the
blind spot: it walks the WHOLE tree for the two coercion fingerprints and bounds the
survivors with an exact count, so a door added tomorrow cannot join the defect silently.
A budget rather than a bare allowlist on purpose — an allowlist with no count is how a
defect tripled behind a green rail.

Both scans are floored against a known-positive control
(:func:`test_the_scanner_can_see_the_defect_it_looks_for`). A rail that inspects nothing
must never read as clean, and this one is an AST matcher over source text — the failure
mode where it silently stops matching is exactly the one that would make every assertion
below vacuous.
"""

from __future__ import annotations

import ast
from typing import Any

import pytest
import write_door_scalar_typing_shared
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from write_door_scalar_typing_shared import _STORE, _CoercionScan

from personalclaw.dashboard.request_boundary import request_boundary_middleware
from personalclaw.request_validation import (
    json_object_body,
    optional_string,
    require_string,
    string_field,
)

_clear_store = write_door_scalar_typing_shared._clear_store


# ── the enumerated half: one row per door ────────────────────────────────────


# ── the blind-spot half: the whole tree, bounded by a count ──────────────────


def test_the_scanner_can_see_the_defect_it_looks_for() -> None:
    """The vacuity floor: both fingerprints, in a handler written the OLD way.

    Without this, a matcher that stopped matching would make
    :func:`test_no_unguarded_name_or_title_coercion_survives` pass over a tree full of the
    defect — the rail would inspect nothing and read as clean.
    """
    control = (
        "async def api_old_create(request):\n"
        "    body = await request.json()\n"
        '    name = str(body.get("name", "")).strip()\n'
        '    title = body["title"].strip()\n'
        "    return name, title\n"
    )
    scan = _CoercionScan()
    scan.visit(ast.parse(control))
    assert scan.hits == {("api_old_create", "name"), ("api_old_create", "title")}


# ── the behavioural half: what the client actually receives ──────────────────


async def _required_name_door(request: web.Request) -> web.Response:
    body = await json_object_body(request)
    record = {"name": require_string(body, "name")}
    _STORE.append(record)
    return web.json_response(record, status=201)


async def _update_name_door(request: web.Request) -> web.Response:
    body = await json_object_body(request)
    name = optional_string(body, "name")
    record = {"name": name if isinstance(name, str) else "<untouched>"}
    _STORE.append(record)
    return web.json_response(record)


async def _optional_title_door(request: web.Request) -> web.Response:
    body = await json_object_body(request)
    record = {"title": string_field(body, "title") or "<derived>"}
    _STORE.append(record)
    return web.json_response(record)


_SHAPES = {
    "require": _required_name_door,
    "optional": _update_name_door,
    "blank-ok": _optional_title_door,
}

#: The five wrong types the issues measured, with what each one used to become.
_WRONG_TYPED = (
    ("object", {"a": "b"}),  # → the stored name "{'a': 'b'}" (#3001)
    ("array", ["x", "y"]),  # → "['x', 'y']"
    ("int", 12345),  # → "12345", which then matched a name regex (#3001)
    ("bool", True),  # → "True"; a bool IS an int, so a (str,int,float) allowlist admits it
    ("null", None),  # → the four-character project name "None" (#456)
)


async def _post(handler, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    app = web.Application(middlewares=[request_boundary_middleware()])
    app.router.add_post("/api/thing", handler)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/thing", json=payload)
        return resp.status, await resp.json()


@pytest.mark.parametrize("shape", sorted(_SHAPES), ids=sorted(_SHAPES))
@pytest.mark.parametrize(("kind", "value"), _WRONG_TYPED, ids=[k for k, _ in _WRONG_TYPED])
@pytest.mark.asyncio
async def test_a_wrong_typed_field_is_a_coded_400_and_stores_nothing(
    shape: str, kind: str, value: Any
) -> None:
    """One rule at every door: a 400 that NAMES the field, and no record written.

    ``null`` is the one legitimate divergence, and it is a semantic choice rather than a
    gap: for a field whose blank is legal, ``null`` and absence mean the same "use the
    default" (``string_field``), so it is accepted and derives a value. It is refused
    everywhere the field must actually hold a string.
    """
    field = "title" if shape == "blank-ok" else "name"
    status, payload = await _post(_SHAPES[shape], {field: value})

    if shape == "blank-ok" and kind == "null":
        assert status == 200
        assert _STORE == [{"title": "<derived>"}]
        return

    assert status == 400, f"{shape}/{kind} answered {status}, not a client error"
    assert payload["error"]["code"] == "field_not_a_string"
    assert field in payload["error"]["message"]
    assert _STORE == [], f"{shape}/{kind} refused at 400 but still wrote {_STORE}"


@pytest.mark.parametrize("shape", sorted(_SHAPES), ids=sorted(_SHAPES))
@pytest.mark.asyncio
async def test_a_real_string_still_gets_through(shape: str) -> None:
    """The floor under every refusal above: the doors are not simply closed."""
    field = "title" if shape == "blank-ok" else "name"
    status, payload = await _post(_SHAPES[shape], {field: "Quarterly review"})
    assert status in (200, 201)
    assert payload[field] == "Quarterly review"
    assert _STORE == [{field: "Quarterly review"}]
