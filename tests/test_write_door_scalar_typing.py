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
import pathlib
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.request_boundary import request_boundary_middleware
from personalclaw.request_validation import (
    json_object_body,
    optional_string,
    require_string,
    string_field,
)

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"

# ── the enumerated half: one row per door ────────────────────────────────────

#: ``(id, module, handler, field, validator)`` — the door table this change closed.
#:
#: ``validator`` is the shape the door's semantics demand, and the three are NOT
#: interchangeable: ``require_string`` for a field the resource cannot exist without,
#: ``optional_string`` for an update door re-asking its create door's rule about a field
#: the caller may simply omit, ``string_field`` for a field whose BLANK is legal and gets
#: derived downstream (an inbox proposal's title falls back to the kind's label) but whose
#: wrong TYPE must still be refused rather than stored as a repr.
#:
#: Ids are short deliberately: a parametrize id built from a payload once reached 100k
#: chars and truncated the CI log that was supposed to name the failure (#2720).
_DOORS: tuple[tuple[str, str, str, str, str], ...] = (
    ("artifact-create", "artifacts/handlers.py", "api_artifacts_create", "name", "require_string"),
    (
        "artifact-folder-update",
        "artifacts/handlers.py",
        "api_artifact_folder_update",
        "name",
        "optional_string",
    ),
    (
        "chat-folder-create",
        "dashboard/chat_folders.py",
        "api_chat_folder_create",
        "name",
        "require_string",
    ),
    (
        "chat-folder-update",
        "dashboard/chat_folders.py",
        "api_chat_folder_update",
        "name",
        "optional_string",
    ),
    ("chat-tag-update", "dashboard/chat_tags.py", "api_chat_tag_update", "name", "optional_string"),
    (
        "session-rename",
        "dashboard/chat_title.py",
        "api_chat_session_rename",
        "title",
        "require_string",
    ),
    ("file-create", "dashboard/handlers/files.py", "api_file_create", "name", "require_string"),
    (
        "loop-rename",
        "dashboard/handlers/loop_routes.py",
        "api_loop_update",
        "name",
        "require_string",
    ),
    ("mcp-toggle", "dashboard/handlers/mcp.py", "api_mcp_toggle", "name", "require_string"),
    ("mcp-remove", "dashboard/handlers/mcp.py", "api_mcp_remove", "name", "require_string"),
    (
        "entity-create",
        "dashboard/handlers/memory.py",
        "api_memory_entity_create",
        "name",
        "require_string",
    ),
    (
        "entity-proposal",
        "dashboard/handlers/memory.py",
        "api_memory_entity_proposals",
        "name",
        "require_string",
    ),
    (
        "provider-create",
        "dashboard/handlers/providers.py",
        "api_provider_create",
        "name",
        "require_string",
    ),
    ("secret-put", "dashboard/handlers/secrets.py", "api_secrets_put", "name", "require_string"),
    (
        "overlay-revert",
        "dashboard/handlers/skills.py",
        "api_skill_overlay_revert",
        "name",
        "require_string",
    ),
    (
        "skill-promote",
        "dashboard/handlers/skills.py",
        "api_ephemeral_skill_promote",
        "title",
        "string_field",
    ),
    ("tool-toggle", "dashboard/handlers/tools.py", "api_tools_toggle", "name", "require_string"),
    (
        "voice-migrate",
        "dashboard/handlers/voice_profiles.py",
        "api_voice_migrate",
        "name",
        "string_field",
    ),
    (
        "proposal-create",
        "dashboard/handlers_inbox.py",
        "api_inbox_proposal_create",
        "title",
        "string_field",
    ),
    ("wf-def-save", "workflows/handlers.py", "api_def_save", "name", "require_string"),
    ("wf-run-start", "workflows/handlers.py", "api_run_start", "name", "require_string"),
)


def _handler(module: str, name: str) -> ast.AST:
    tree = ast.parse((_SRC / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{module} no longer defines {name}() — the door table is stale")


def _validator_calls(handler: ast.AST) -> set[tuple[str, str]]:
    """``{(validator_name, field)}`` for every shared-validator call inside *handler*."""
    found: set[tuple[str, str]] = set()
    for node in ast.walk(handler):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in {"require_string", "optional_string", "string_field"}:
            continue
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            # A field name is a string literal; narrowing here (rather than trusting
            # `ast.Constant.value`, which is typed as any literal) keeps the set's own
            # type honest and ignores a call whose second argument is not a name at all.
            if isinstance(node.args[1].value, str):
                found.add((node.func.id, node.args[1].value))
    return found


def _presence_gated(handler: ast.AST, field: str) -> bool:
    """True when *handler* tests ``field in body`` — the other spelling of OPTIONAL.

    ``optional_string(body, f)`` and ``require_string(body, f) if f in body else None``
    ask the SAME two questions in the same order: is the field present, and if so is it
    a real non-blank ``str``. The presence test is what makes the second form optional,
    so it is the thing this helper looks for: a door that calls ``require_string`` with
    no presence test genuinely REQUIRES the field and must still fail the row below.
    """
    for node in ast.walk(handler):
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Constant)
            and node.left.value == field
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.In)
        ):
            return True
    return False


@pytest.mark.parametrize(
    ("module", "handler", "field", "validator"),
    [row[1:] for row in _DOORS],
    ids=[row[0] for row in _DOORS],
)
def test_door_routes_its_name_through_the_shared_validator(
    module: str, handler: str, field: str, validator: str
) -> None:
    """The door asks the ONE shared question about its own ``name``/``title``."""
    node = _handler(module, handler)
    calls = _validator_calls(node)
    accepted = f"{validator}(body, {field!r})"
    satisfied = (validator, field) in calls
    if not satisfied and validator == "optional_string":
        # The OPTIONAL semantic, spelled the other way. Three update doors (chat-tag,
        # chat-folder, artifact-folder) landed on `main` as a presence-gated
        # `require_string` inside a local `RequestValidationError` wrapper, because those
        # modules answer flat everywhere and a nested refusal for `name` alone would split
        # one endpoint across two envelope shapes. That is the same rule this row exists to
        # pin — same validator, same refusals — so the row accepts both spellings and keeps
        # its teeth via `_presence_gated`, which is the half that makes the form optional.
        satisfied = ("require_string", field) in calls and _presence_gated(node, field)
        accepted += f" (or a presence-gated require_string(body, {field!r}))"
    assert satisfied, (
        f"{module}:{handler} no longer calls {accepted}. "
        f"Found: {sorted(calls) or 'nothing'}. A door that reads its name any other way "
        f"invents a third answer for a wrong type — that is #3001/#456."
    )


# ── the blind-spot half: the whole tree, bounded by a count ──────────────────

#: The ONLY surviving ``.strip()``-on-a-raw-body-read sites, and why they are not the
#: defect: ``api_themes_create``/``api_theme_detail`` both run ``_validate_theme_data``
#: FIRST, which refuses a non-string ``name`` with a message naming the field before the
#: ``.strip()`` is ever reached. Their refusal is correct; only its envelope is the older
#: flat ``{"error": ...}`` shape, and reshaping a correct refusal is an error-envelope
#: decision this change does not make.
#:
#: The exact count is the point. A pair list with no number lets a third site join the
#: exception quietly; a number makes any new site red.
_GUARDED_ELSEWHERE: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("dashboard/handlers/agents.py", "api_themes_create", "name"),
        ("dashboard/handlers/agents.py", "api_theme_detail", "name"),
    }
)

_TARGET_FIELDS = frozenset({"name", "title"})
_BODY_LOCALS = frozenset({"body", "fields", "payload", "raw", "data"})


class _CoercionScan(ast.NodeVisitor):
    """Finds ``str(<body read>)`` and ``<body read>.strip()`` inside ``api_*`` handlers.

    These are the two fingerprints the issues measured. ``str()`` is the one that PERSISTS
    a repr (``{"name": {"a": "b"}}`` → the stored name ``{'a': 'b'}``); the bare ``.strip()``
    is the one that raises ``AttributeError`` into the request-shape boundary, which answers
    a generic ``bad_request`` naming no field — a swallowed crash rather than validation.
    """

    def __init__(self) -> None:
        self.fn: str | None = None
        self.hits: set[tuple[str, str]] = set()

    def visit_AsyncFunctionDef(self, node: Any) -> None:
        outer = self.fn
        if node.name.startswith("api_"):
            self.fn = node.name
        self.generic_visit(node)
        self.fn = outer

    visit_FunctionDef = visit_AsyncFunctionDef  # nested sync helpers count as the door

    @staticmethod
    def _field_read(node: ast.AST) -> str | None:
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Subscript)
                and isinstance(inner.value, ast.Name)
                and inner.value.id in _BODY_LOCALS
                and isinstance(inner.slice, ast.Constant)
                and inner.slice.value in _TARGET_FIELDS
            ):
                return str(inner.slice.value)
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "get"
                and isinstance(inner.func.value, ast.Name)
                and inner.func.value.id in _BODY_LOCALS
                and inner.args
                and isinstance(inner.args[0], ast.Constant)
                and inner.args[0].value in _TARGET_FIELDS
            ):
                return str(inner.args[0].value)
        return None

    def visit_Call(self, node: ast.Call) -> None:
        if self.fn:
            if isinstance(node.func, ast.Name) and node.func.id == "str":
                field = self._field_read(node)
                if field:
                    self.hits.add((self.fn, field))
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "strip":
                inner = node.func.value
                already_counted = (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "str"
                )
                field = self._field_read(inner)
                if field and not already_counted:
                    self.hits.add((self.fn, field))
        self.generic_visit(node)


def _scan_tree() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    for path in sorted(_SRC.rglob("*.py")):
        scan = _CoercionScan()
        scan.visit(ast.parse(path.read_text(encoding="utf-8")))
        rel = path.relative_to(_SRC).as_posix()
        found |= {(rel, fn, field) for fn, field in scan.hits}
    return found


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


def test_no_unguarded_name_or_title_coercion_survives() -> None:
    """No ``api_*`` handler coerces a ``name``/``title`` it never type-checked."""
    survivors = _scan_tree()
    assert survivors == _GUARDED_ELSEWHERE, (
        "write doors coercing a name/title outside the shared validator:\n  "
        + "\n  ".join(f"{m}:{fn} [{f}]" for m, fn, f in sorted(survivors - _GUARDED_ELSEWHERE))
        + "\nEach one invents its own answer for a wrong type — a persisted Python repr, or an "
        "AttributeError the request boundary flattens into a field-less 400. Route it through "
        "personalclaw.request_validation instead (#3001, #456)."
    )
    assert len(survivors) == len(_GUARDED_ELSEWHERE) == 2


# ── the behavioural half: what the client actually receives ──────────────────

#: Everything a door "persisted", so a test can assert a refusal wrote NOTHING. A status
#: code alone would miss the defect these issues are about: the damage was a stored value,
#: not a wrong number on the wire.
_STORE: list[dict[str, Any]] = []


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


@pytest.fixture(autouse=True)
def _clear_store():
    _STORE.clear()
    yield
    _STORE.clear()


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
