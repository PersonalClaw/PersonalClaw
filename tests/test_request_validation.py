"""The one request-body reader and the one string-field check, behaviourally (#2923, #3001).

The structural half — "no module defines its own body reader" — lives in
``test_one_request_body_reader.py``. This file asserts what the one reader DOES, and the
first test is the whole point of the change: a reader annotated ``-> dict`` could not refuse
a malformed body, so nineteen private readers ended ``except Exception: return {}`` and a
truncated request minted a real defaulted record at 200 (#2923).

``test_without_the_shared_reader_a_malformed_body_is_a_200`` is the anti-fabrication proof,
in the shape ``test_api_request_boundary_envelope`` uses: the SAME request through a handler
written the OLD way answers 200 with a defaulted record, and 400 with a coded envelope
through the new one. Without it, every assertion below could be satisfied by a reader that
was never wired to anything.
"""

from __future__ import annotations

from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.request_boundary import request_boundary_middleware
from personalclaw.request_validation import (
    MISSING,
    RequestValidationError,
    json_object_body,
    optional_string,
    require_string,
    string_field,
)

# ── the two handler shapes, old and new, over one identical route ────────────

#: Everything the OLD-shape handler persisted, so a test can assert that a malformed body
#: did (or did not) create a record. This is the part a status-code-only test misses.
_STORE: list[dict[str, Any]] = []


async def _old_shape_private_reader(request: web.Request) -> dict:
    """`handlers/autonomy._body` verbatim — the eight-module `-> dict` contract.

    Reproduced rather than referenced because the original is deleted; the invariant this
    file guards is that this SHAPE stays deleted, and the shape has to exist somewhere for
    the comparison below to be a measurement instead of a claim.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


async def _create_the_old_way(request: web.Request) -> web.Response:
    body = await _old_shape_private_reader(request)
    record = {"name": str(body.get("name", ""))}
    _STORE.append(record)
    return web.json_response({"ok": True, **record})


async def _create_the_new_way(request: web.Request) -> web.Response:
    body = await json_object_body(request)
    record = {"name": require_string(body, "name")}
    _STORE.append(record)
    return web.json_response({"ok": True, **record})


def _app(handler) -> web.Application:
    app = web.Application(middlewares=[request_boundary_middleware()])
    app.router.add_post("/api/thing", handler)
    return app


@pytest.fixture(autouse=True)
def _clear_store():
    _STORE.clear()
    yield
    _STORE.clear()


async def _envelope(resp) -> dict:
    assert resp.content_type == "application/json"
    return (await resp.json())["error"]


# ── #2923: the defect, and that it is gone ───────────────────────────────────


@pytest.mark.asyncio
async def test_without_the_shared_reader_a_malformed_body_is_a_200() -> None:
    """The anti-fabrication baseline: the old shape ACCEPTS a truncated body and writes.

    Not a regression test — a measurement of what is being replaced. If this ever fails,
    the comparison below has lost its meaning and the "structurally cannot refuse" claim in
    both docstrings needs rewriting rather than trusting.
    """
    async with TestClient(TestServer(_app(_create_the_old_way))) as client:
        resp = await client.post(
            "/api/thing", data='{"name": "half', headers={"Content-Type": "application/json"}
        )
        assert resp.status == 200
        assert (await resp.json()) == {"ok": True, "name": ""}
    # And the damage a status code does not show: a real record, named "".
    assert _STORE == [{"name": ""}], "the old shape persisted a defaulted record"


@pytest.mark.asyncio
async def test_a_malformed_body_is_refused_and_nothing_is_written() -> None:
    """#2923: the same truncated body is a 400 `invalid_json`, and writes nothing."""
    async with TestClient(TestServer(_app(_create_the_new_way))) as client:
        resp = await client.post(
            "/api/thing", data='{"name": "half', headers={"Content-Type": "application/json"}
        )
        assert resp.status == 400
        assert (await _envelope(resp))["code"] == "invalid_json"
    assert _STORE == [], "a refused request must not have persisted anything"


@pytest.mark.asyncio
async def test_a_body_that_is_not_an_object_is_refused() -> None:
    """A JSON array parses fine and is still not a body.

    `invalid_body`, not `invalid_json`: the bytes were valid JSON, so telling the caller
    their JSON is broken would send them looking in the wrong place.
    """
    async with TestClient(TestServer(_app(_create_the_new_way))) as client:
        resp = await client.post("/api/thing", json=[1, 2, 3])
        assert resp.status == 400
        err = await _envelope(resp)
        assert err["code"] == "invalid_body"
        assert "array" in err["message"], "the refusal speaks JSON, not Python (`list`)"
    assert _STORE == []


@pytest.mark.asyncio
async def test_the_refusal_names_the_offending_field() -> None:
    """#3001: `{"name": {...}}` is refused BY NAME instead of stored as Python's repr.

    The field name is the reason `RequestValidationError` is caught ahead of the boundary's
    generic `bad_request` branch — routed through that branch this would say nothing about
    `name`, and a nameless 400 is what made the coercion so hard to find.
    """
    async with TestClient(TestServer(_app(_create_the_new_way))) as client:
        resp = await client.post("/api/thing", json={"name": {"a": "b"}})
        assert resp.status == 400
        err = await _envelope(resp)
        assert err["code"] == "field_not_a_string"
        assert "name" in err["message"] and "an object" in err["message"]
    assert _STORE == [], "the repr must never reach the store"


@pytest.mark.asyncio
async def test_a_well_formed_request_still_succeeds() -> None:
    """The floor: the gate refuses malformed input without refusing the valid case too."""
    async with TestClient(TestServer(_app(_create_the_new_way))) as client:
        resp = await client.post("/api/thing", json={"name": "  ok  "})
        assert resp.status == 200
        assert (await resp.json()) == {"ok": True, "name": "ok"}, "the value arrives stripped"
    assert _STORE == [{"name": "ok"}]


@pytest.mark.asyncio
async def test_an_absent_body_is_an_empty_object_not_a_refusal() -> None:
    """`empty_ok` is about absent BYTES, never about bytes that failed to parse.

    Every route whose fields are all optional wants `{}` for a bodyless POST; conflating
    that with a parse failure is how the `except: return {}` shape justified itself.
    """

    async def _optional_fields(request: web.Request) -> web.Response:
        return web.json_response({"body": await json_object_body(request)})

    async with TestClient(TestServer(_app(_optional_fields))) as client:
        resp = await client.post("/api/thing")
        assert resp.status == 200
        assert (await resp.json()) == {"body": {}}


@pytest.mark.asyncio
async def test_a_body_present_without_can_read_body_is_still_read() -> None:
    """Emptiness is decided by the BYTES, never by aiohttp's `can_read_body` flag.

    The first cut of the reader short-circuited on `not request.can_read_body` before
    parsing. That flag describes the transport, not the content, so anything carrying a
    body without satisfying it received `{}` while holding a good payload — measured as
    ~100 handler-test failures across `test_loop_http`, `test_workflows_api` and
    `test_knowledge_restructure`, all of which drive real handlers through
    `make_mocked_request` doubles. A reader whose emptiness test can disagree with its own
    bytes has the nineteen readers' defect one layer down, so the flag is gone and this
    pins it out.
    """
    from aiohttp.test_utils import make_mocked_request

    request = make_mocked_request("POST", "/api/thing")
    assert not request.can_read_body, "the premise: the flag says there is nothing to read"

    async def _payload() -> dict[str, Any]:
        return {"name": "real"}

    request.json = _payload  # type: ignore[assignment]
    assert await json_object_body(request) == {"name": "real"}


@pytest.mark.asyncio
async def test_empty_ok_false_refuses_a_bodyless_request() -> None:
    """A route that REQUIRES a body says so once, and gets the same coded refusal."""

    async def _needs_a_body(request: web.Request) -> web.Response:
        return web.json_response({"body": await json_object_body(request, empty_ok=False)})

    async with TestClient(TestServer(_app(_needs_a_body))) as client:
        resp = await client.post("/api/thing")
        assert resp.status == 400
        assert (await _envelope(resp))["code"] == "invalid_json"


# ── the three field shapes ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,code",
    [
        (None, "field_not_a_string"),  # `null` — became the name "None" (#456)
        (1, "field_not_a_string"),
        (1.5, "field_not_a_string"),
        (True, "field_not_a_string"),  # a bool IS an int in Python — the session_templates bug
        ([], "field_not_a_string"),
        ({}, "field_not_a_string"),
        ("", "field_required"),  # present but blank (#2992)
        ("   ", "field_required"),  # blank once stripped
    ],
)
def test_require_string_refuses_every_non_string_and_every_blank(value, code) -> None:
    with pytest.raises(RequestValidationError) as exc:
        require_string({"name": value}, "name")
    assert exc.value.code == code
    assert exc.value.status == 400
    assert "name" in exc.value.message, "the message must name the field"


def test_require_string_refuses_an_absent_field() -> None:
    with pytest.raises(RequestValidationError) as exc:
        require_string({}, "name")
    assert exc.value.code == "field_required"


def test_require_string_returns_the_stripped_value() -> None:
    """Stripped once, here, so no call site re-strips — or forgets to."""
    assert require_string({"name": "  hi  "}, "name") == "hi"
    assert require_string({"name": "  hi  "}, "name", strip=False) == "  hi  "


def test_string_field_defaults_for_absent_and_null_but_refuses_a_wrong_type() -> None:
    """The `POST /api/knowledge/items` `title` shape: blank is legal, an object is not."""
    assert string_field({}, "title") == ""
    assert string_field({"title": None}, "title") == ""
    assert string_field({"title": ""}, "title") == ""
    assert string_field({}, "title", default="untitled") == "untitled"
    assert string_field({"title": " x "}, "title") == "x"
    with pytest.raises(RequestValidationError) as exc:
        string_field({"title": {"a": "b"}}, "title")
    assert exc.value.code == "field_not_a_string"


def test_optional_string_distinguishes_absent_from_blank() -> None:
    """#2970: a PATCH that omits a field is not a PATCH that clears it."""
    assert optional_string({}, "name") is MISSING
    assert optional_string({"name": " hi "}, "name") == "hi"
    with pytest.raises(RequestValidationError) as exc:
        optional_string({"name": ""}, "name")
    assert exc.value.code == "field_required", "a blank is a refusal, not an instruction to clear"


def test_missing_has_no_truth_value() -> None:
    """`if value:` at a call site is the missing-vs-blank confusion; make it loud, not silent."""
    with pytest.raises(TypeError, match="is MISSING"):
        bool(MISSING)


# ── the error type's own contract ────────────────────────────────────────────


def test_the_error_is_not_a_builtin_the_boundary_already_swallows() -> None:
    """Subclassing ValueError/TypeError/AttributeError would discard the field name.

    The boundary catches those three as the UNGUARDED fault family and answers a generic
    `bad_request` that names nothing. A refusal that inherited from one of them would be
    routed there by `except` ordering and lose the only thing it added.
    """
    assert not issubclass(RequestValidationError, (ValueError, TypeError, AttributeError))


def test_the_error_carries_its_own_envelope() -> None:
    exc = RequestValidationError("field_required", "name is required.")
    resp = exc.response
    assert resp.status == 400
    assert resp.content_type == "application/json"


@pytest.mark.asyncio
async def test_a_refusal_off_the_api_surface_is_still_answered() -> None:
    """Unlike the generic fault branch, a DELIBERATE refusal is not /api-scoped.

    It is a formed answer for any route that chose to validate, not a fault being rescued on
    one surface — so a non-/api route that validates must not fall through to a bare 500.
    """
    app = web.Application(middlewares=[request_boundary_middleware()])
    app.router.add_post("/notapi/thing", _create_the_new_way)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/notapi/thing", json={"name": 7})
        assert resp.status == 400
        assert (await _envelope(resp))["code"] == "field_not_a_string"


def test_every_code_this_module_raises_is_a_registered_literal() -> None:
    """Every `RequestValidationError` code is a bare literal AND has a registry row.

    This is the rail that lets `test_http_error_codes_append_only`'s dynamic-site ceiling
    accommodate `RequestValidationError.response`. That emitter passes `self.code`, which
    the static registry scanner cannot resolve, so the site reads as dynamic — the same
    shape as `inbound/openai_dialect.openai_error`, the 17th site, and closed the same way:
    one level up, where the indirection actually is.

    Enumerated from the SOURCE, not from a list in this test. A hand-written tuple of the
    four current codes would pass forever while a fifth was added unregistered — an
    enumerated rail cannot see its own blind spot, which is the failure mode this whole
    change is written against.
    """
    import ast
    import pathlib

    import personalclaw
    from personalclaw.http_errors import HTTP_ERROR_CODES

    source = (pathlib.Path(personalclaw.__file__).parent / "request_validation.py").read_text()
    codes: list[str] = []
    computed: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        call = node.exc
        if not isinstance(call, ast.Call):
            continue
        name = call.func.id if isinstance(call.func, ast.Name) else ""
        if name != "RequestValidationError":
            continue
        first = call.args[0] if call.args else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            codes.append(first.value)
        else:
            computed.append(node.lineno)

    assert codes, "found no RequestValidationError raises — the AST matcher is broken"
    assert not computed, (
        "these raises compute their wire code, so no static reader can tell whether it is "
        f"registered: lines {computed}. Pass a literal."
    )
    unregistered = sorted({c for c in codes if c not in HTTP_ERROR_CODES})
    assert not unregistered, (
        f"raised but absent from HTTP_ERROR_CODES: {unregistered}. A wire code needs its "
        "registry row in the same change that ships it."
    )
