"""Every boolean a request body carries is the JSON true or false, and a stored switch reads as the
word it spells.

``bool("false")`` is True. A door that read its switch or its consent by truthiness turned on what
its caller turned off: the one-link pack import read ``"consent": "false"`` as consent, a trigger
toggled with ``"enabled": "false"`` was switched on, and an inbox update sent
``"mute_thread": "false"`` muted the thread. Its stored half kept the defect after the request was
gone: a provider instance saved with ``"enabled": "false"`` was read back on, and
``security.egress.allow_private: "false"`` in ``config.json`` let the egress guard reach private
addresses.

The two sources take two rules, for one reason. A request body can always carry a real boolean, so
a door reads its booleans through ``request_validation`` and refuses anything else with a 400 that
names the field (``bool_field`` / ``require_bool`` / ``optional_bool``, or a hand-written
``not isinstance(value, bool)`` refusal of the same kind). A stored setting cannot be refused once
it is written, so it reads as the word it spells, with the switch's safe value for a value that
spells neither (``safety_flags.strict_bool``); ``config.json`` does it once, for every boolean the
schema declares, in its validation pass.

This census keeps both so:

* :data:`BODY_BOOLEANS` pins every boolean a door reads, with what an omitted field means there. A
  door that starts reading one fails :func:`test_every_body_boolean_is_pinned` until it is pinned,
  and a read of one by truthiness fails the ban below it.
* :data:`LOADER_BUDGET` is the exact set of record loaders that still read a boolean with
  ``bool()``: records the product writes itself. A new one fails, and so does an app manifest's.
* Every boolean in the ``config.json`` schema is driven through the loader with text in it.
* An MCP server's ``disabled`` switch has one reader, ``mcp_status.switched_off``.

``tests/test_tool_boolean_census.py`` is the sibling for a model's tool arguments, which arrive as
text as often as not and are read as the word they spell (``safety_flags.yes_or_no``).
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import personalclaw

SRC = Path(personalclaw.__file__).resolve().parent

_READERS = {"bool_field", "require_bool", "optional_bool"}
#: Helper parameters that hold a request body (`_build_loop_from_body(body)`, `_fields(body)`).
_BODY_PARAMS = {"body", "payload"}

#: What an omitted field means at each door: the ``bool_field`` default (``None`` when the door
#: leaves the decision downstream), ``"required"`` (``require_bool``), ``"optional"``
#: (``optional_bool``: left out, the setting stays as it is), or ``"checked"`` (a hand-written
#: ``not isinstance(value, bool)`` refusal that names the field; each of those carries a contract
#: of its own, a security-log row or a message saying what the switch does).
BODY_BOOLEANS: dict[tuple[str, str, str], object] = {
    ("artifacts/handlers.py", "api_artifact_update", "snapshot"): False,
    ("artifacts/handlers.py", "api_artifacts_pin", "pinned"): True,
    ("dashboard/chat_folders.py", "api_chat_folder_update", "collapsed"): "optional",
    ("dashboard/chat_folders.py", "api_chat_session_pin", "pinned"): "required",
    ("dashboard/chat_handlers.py", "api_chat_session_context", "ephemeral"): True,
    ("dashboard/chat_questions.py", "api_chat_question_answer", "skip"): False,
    ("dashboard/chat_handlers.py", "api_chat_session_create", "ephemeral"): False,
    ("dashboard/chat_handlers.py", "api_chat_sessions_cleanup", "dry_run"): False,
    ("dashboard/chat_regenerate.py", "api_chat_session_edit_resend", "rewind"): False,
    ("dashboard/chat_tags.py", "api_chat_tag_create", "status"): False,
    ("dashboard/chat_tags.py", "api_chat_tag_update", "status"): "optional",
    ("dashboard/handlers/autonudge.py", "api_autonudge_update", "active"): None,
    ("dashboard/handlers/core.py", "api_project_trust", "trusted"): "required",
    ("dashboard/handlers/core.py", "api_sel_rotate", "archive"): True,
    (
        "dashboard/handlers/external_access.py",
        "api_external_access_client_toggle",
        "disabled",
    ): "required",
    ("dashboard/handlers/files.py", "api_dashboard_config", "auto_tag_sessions"): "checked",
    ("dashboard/handlers/files.py", "api_dashboard_config", "document_editing"): "checked",
    ("dashboard/handlers/files.py", "api_dashboard_config", "followup_chips"): "checked",
    ("dashboard/handlers/files.py", "api_dashboard_config", "merge_queued_messages"): "checked",
    ("dashboard/handlers/files.py", "api_dashboard_config", "offer_check_work"): "checked",
    ("dashboard/handlers/files.py", "api_dashboard_config", "restore_sessions"): "checked",
    ("dashboard/handlers/files.py", "api_dashboard_config", "screen_share_enabled"): "checked",
    ("dashboard/handlers/files.py", "api_dashboard_config", "send_on_enter"): "checked",
    ("dashboard/handlers/files.py", "api_dashboard_config", "show_thinking_inline"): "checked",
    ("dashboard/handlers/files.py", "api_dashboard_config", "show_timestamps"): "checked",
    ("dashboard/handlers/files.py", "api_dashboard_config", "simplified_tool_names"): "checked",
    ("dashboard/handlers/hooks.py", "api_hooks_agent", "deliver"): True,
    ("dashboard/handlers/knowledge.py", "bulk_items", "value"): "required",
    ("dashboard/handlers/knowledge.py", "restructure_item", "relink"): True,
    ("dashboard/handlers/knowledge.py", "set_item_favorited", "value"): True,
    ("dashboard/handlers/knowledge.py", "update_item", "is_archived"): "required",
    ("dashboard/handlers/knowledge.py", "update_item", "is_pinned"): "required",
    ("dashboard/handlers/knowledge.py", "update_item", "reingest"): True,
    ("dashboard/handlers/knowledge.py", "update_watched_source", "enabled"): "optional",
    ("dashboard/handlers/local_model.py", "api_local_model_bind", "bind_chat"): "checked",
    ("dashboard/handlers/loop_routes.py", "_build_loop_from_body", "attended"): True,
    (
        "dashboard/handlers/loop_routes.py",
        "_build_loop_from_body",
        "auto_teardown_on_complete",
    ): False,
    ("dashboard/handlers/loop_routes.py", "_build_loop_from_body", "autopilot"): True,
    ("dashboard/handlers/loop_routes.py", "_create_ported_kind_as_run", "attended"): "optional",
    ("dashboard/handlers/loop_routes.py", "api_loop_autopilot", "on"): "required",
    ("dashboard/handlers/mcp.py", "api_mcp_toggle", "enabled"): "required",
    ("dashboard/handlers/mcp.py", "api_mcp_toggle_all", "enabled"): "required",
    ("dashboard/handlers/mcp.py", "api_mcp_toggle_tool", "enabled"): "required",
    ("dashboard/handlers/memory.py", "api_memory_approval_rule_add", "send_capable"): False,
    ("dashboard/handlers/memory.py", "api_memory_consolidate", "include_history"): True,
    ("dashboard/handlers/memory.py", "api_memory_facet_pin", "pinned"): True,
    ("dashboard/handlers/messaging.py", "api_send_message", "dry_run"): False,
    ("dashboard/handlers/messaging.py", "api_send_message", "reply_broadcast"): None,
    ("dashboard/handlers/messaging.py", "api_send_message", "unfurl_links"): None,
    ("dashboard/handlers/messaging.py", "api_send_message", "unfurl_media"): None,
    ("dashboard/handlers/messaging.py", "api_spawn", "silent"): False,
    ("dashboard/handlers/packs.py", "api_pack_one_link", "consent"): False,
    ("dashboard/handlers/research_reports.py", "_fields", "enabled"): "checked",
    ("dashboard/handlers/tools.py", "api_providers_toggle", "enabled"): "required",
    ("dashboard/handlers/tools.py", "api_tool_invoke", "dry_run"): False,
    ("dashboard/handlers/tools.py", "api_tools_toggle", "enabled"): "required",
    ("dashboard/handlers/trigger_callbacks.py", "toggle", "enabled"): None,
    ("dashboard/handlers/trigger_runs.py", "_run_store", "dry_run"): False,
    ("dashboard/handlers/trigger_runs.py", "api_trigger_answer", "answer"): "required",
    ("dashboard/handlers/triggers.py", "_create_schedule", "catch_up"): False,
    ("dashboard/handlers/triggers.py", "_create_schedule", "enabled"): True,
    ("dashboard/handlers/triggers.py", "_create_schedule", "failure_dedupe"): False,
    ("dashboard/handlers/triggers.py", "_create_schedule", "silent"): False,
    ("dashboard/handlers/triggers.py", "_create_schedule", "strict_schedule"): False,
    ("dashboard/handlers/triggers.py", "_grant_for_save", "enabled"): False,
    ("dashboard/handlers/triggers.py", "_update_schedule", "catch_up"): "optional",
    ("dashboard/handlers/triggers.py", "_update_schedule", "failure_dedupe"): "optional",
    ("dashboard/handlers/triggers.py", "_update_schedule", "silent"): "optional",
    ("dashboard/handlers/triggers.py", "_update_schedule", "strict_schedule"): "optional",
    ("dashboard/handlers/triggers.py", "api_trigger_toggle", "enabled"): None,
    ("dashboard/handlers/updates.py", "api_update_simulate", "reject"): False,
    ("dashboard/handlers/views.py", "api_dashboard_view_tile_refresh", "force"): False,
    ("dashboard/handlers/views.py", "api_dashboard_view_tile_resolve", "keep"): "required",
    ("dashboard/handlers_inbox.py", "api_inbox_favorite", "favorited"): True,
    ("dashboard/handlers_inbox.py", "api_inbox_proposal_create", "editable"): False,
    ("dashboard/handlers_inbox.py", "api_inbox_update", "mute_thread"): False,
    ("dashboard/session_bulk.py", "api_chat_session_lifecycle", "never_archive"): "optional",
    ("dashboard/session_bulk.py", "api_chat_sessions_auto_archive", "dry_run"): False,
    ("dashboard/session_bulk.py", "api_chat_sessions_bulk", "value"): True,
    ("lexicon/handlers.py", "api_lexicon_add_correction", "always"): False,
    ("lexicon/handlers.py", "api_lexicon_update_correction", "auto_apply"): "required",
    ("lexicon/handlers.py", "api_lexicon_update_term", "enabled"): "required",
    ("providers/instance_routes.py", "handle_set_use_case_settings", "auto_speak"): "optional",
    ("providers/instance_routes.py", "handle_set_use_case_settings", "enabled"): "optional",
    ("providers/instance_routes.py", "handle_update_instance", "enabled"): None,
    ("tasks/hierarchy_handlers.py", "api_projects_create", "name_locked"): False,
    ("tasks/hierarchy_handlers.py", "api_projects_update", "name_locked"): "optional",
    ("tasks/hierarchy_handlers.py", "api_task_lists_create", "repeatable"): False,
    ("workflows/handlers.py", "_reentry", "force"): False,
    ("workflows/handlers.py", "_reentry", "redo_effects"): False,
    ("workflows/handlers.py", "_save_def", "save"): True,
    ("workflows/handlers.py", "_save_def", "strict"): True,
    ("workflows/handlers.py", "api_agent_save", "save"): True,
    ("workflows/handlers.py", "api_def_a2a_publish", "published"): False,
    ("workflows/handlers.py", "api_run_edit", "preview_only"): False,
    ("workflows/handlers.py", "api_run_resume", "always_allow"): False,
    ("workflows/handlers.py", "api_run_review_triage", "dry_run"): False,
    ("workflows/handlers.py", "api_run_start", "skip_preflight"): False,
}

#: Fields that grant a consent, publish, or widen what runs. An omitted one is never a yes.
_WIDENING = {
    "consent",
    "always_allow",
    "skip_preflight",
    "force",
    "redo_effects",
    "send_capable",
    "published",
    "trusted",
    "auto_apply",
}

#: Names that are booleans at the doors above and text at others (a status word, a binding's or a
#: secret's value, a workflow gate's answer), so a presence test of them is not a boolean read.
_AMBIGUOUS = {"status", "value", "answer"}


def _body_module(text: str) -> bool:
    return (
        "json_object_body(" in text or "await request.json()" in text or "await req.json()" in text
    )


def _constant_strings(node: ast.AST | None, module_tuples: dict[str, list[str]]) -> list[str]:
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [
            e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
    if isinstance(node, ast.Name):
        return module_tuples.get(node.id, [])
    return []


def _functions(tree: ast.AST) -> Iterator[tuple[ast.AST, list[ast.AST]]]:
    """Each function with the nodes that are its own: a nested function's nodes are its own."""
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        own: list[ast.AST] = []
        stack = list(ast.iter_child_nodes(fn))
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            own.append(node)
            stack.extend(ast.iter_child_nodes(node))
        yield fn, own


def _is_body_call(node: ast.AST | None) -> bool:
    """``await request.json()`` or ``await json_object_body(...)``."""
    if not (isinstance(node, ast.Await) and isinstance(node.value, ast.Call)):
        return False
    func = node.value.func
    if isinstance(func, ast.Name):
        return func.id == "json_object_body"
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "json"
        and isinstance(func.value, ast.Name)
        and func.value.id in {"request", "req"}
    )


def _scan(source: str) -> tuple[dict[tuple[str, str], object], list[tuple[str, str, str, int]]]:
    """``(declared, misreads)`` for one module.

    *declared* maps ``(function, field)`` to what an omitted field means there, for every boolean
    the function reads through a reader or refuses by hand. *misreads* lists ``(function, field,
    shape, line)`` for every read of a body field by truthiness: ``bool()``, ``not``, an
    ``if``/``while``/ternary/``assert``/comprehension test, an ``and``/``or`` operand, a comparison
    with ``True``/``False``, or a ``.get(field, True|False)`` default. A name bound straight from a
    raw read counts as the read, unless a hand-written refusal checks it in between.
    """
    tree = ast.parse(source)
    module_tuples = {
        t.id: _constant_strings(n.value, {})
        for n in getattr(tree, "body", [])
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    declared: dict[tuple[str, str], object] = {}
    misreads: list[tuple[str, str, str, int]] = []
    for fn, own in _functions(tree):
        assert isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        params = {a.arg for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)}
        bodies = params & _BODY_PARAMS
        for node in own:
            if isinstance(node, ast.Assign) and _is_body_call(node.value):
                bodies |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, ast.NamedExpr) and _is_body_call(node.value):
                bodies.add(node.target.id)
        parent: dict[ast.AST, ast.AST] = {}
        for node in [fn, *own]:
            for child in ast.iter_child_nodes(node):
                parent[child] = node

        def fields_of(arg: ast.AST) -> list[str]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return [arg.value]
            if isinstance(arg, ast.Name):
                node: ast.AST = arg
                while node in parent:
                    node = parent[node]
                    if (
                        isinstance(node, ast.For)
                        and isinstance(node.target, ast.Name)
                        and node.target.id == arg.id
                    ):
                        return _constant_strings(node.iter, module_tuples)
            return []

        def read(node: ast.AST) -> list[str]:
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in bodies
                and node.args
            ):
                return fields_of(node.args[0])
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id in bodies
            ):
                return fields_of(node.slice)
            return []

        for node in own:
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _READERS
                and len(node.args) >= 2
            ):
                how: object = {"require_bool": "required", "optional_bool": "optional"}.get(
                    node.func.id
                )
                if how is None:
                    given = [k.value for k in node.keywords if k.arg == "default"]
                    how = given[0].value if given and isinstance(given[0], ast.Constant) else "?"
                for field in fields_of(node.args[1]):
                    declared[(fn.name, field)] = how

        bindings: list[tuple[int, str, list[str]]] = []
        for node in own:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target: ast.AST = node.targets[0]
            elif isinstance(node, ast.NamedExpr):
                target = node.target
            else:
                continue
            if isinstance(target, ast.Name) and (fields := read(node.value)):
                bindings.append((node.lineno, target.id, fields))

        def binding(name: str, line: int) -> tuple[int, list[str]] | None:
            nearest = [(ln, fs) for ln, nm, fs in bindings if nm == name and ln <= line]
            return max(nearest, key=lambda b: b[0]) if nearest else None

        checks: list[tuple[int, str]] = []
        for node in own:
            if not (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)):
                continue
            call = node.operand
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "isinstance"
                and len(call.args) == 2
                and isinstance(call.args[1], ast.Name)
                and call.args[1].id == "bool"
            ):
                continue
            checked = call.args[0]
            fields = read(checked)
            if isinstance(checked, ast.Name):
                checks.append((call.lineno, checked.id))
                found = binding(checked.id, call.lineno)
                fields = found[1] if found else []
            for field in fields:
                declared[(fn.name, field)] = "checked"

        def key_of(node: ast.AST) -> list[str]:
            if fields := read(node):
                return fields
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                found = binding(node.id, node.lineno)
                if found and not any(
                    name == node.id and found[0] <= line <= node.lineno for line, name in checks
                ):
                    return found[1]
            return []

        for node in own:
            tested: list[tuple[str, ast.AST]] = []
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "bool":
                    tested += [("bool()", a) for a in node.args]
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                tested.append(("not", node.operand))
            elif isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
                tested.append(("test", node.test))
            elif isinstance(node, ast.comprehension):
                tested += [("test", i) for i in node.ifs]
            elif isinstance(node, ast.BoolOp):
                tested += [("and/or", v) for v in node.values]
            elif isinstance(node, ast.Compare):
                sides = [node.left, *node.comparators]
                if any(isinstance(s, ast.Constant) and isinstance(s.value, bool) for s in sides):
                    tested += [("compare", s) for s in sides]
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and len(node.args) == 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, bool)
            ):
                misreads += [(fn.name, f, "default", node.lineno) for f in read(node)]
            for shape, expr in tested:
                misreads += [(fn.name, f, shape, getattr(expr, "lineno", 0)) for f in key_of(expr)]
    return declared, misreads


def _tree() -> tuple[dict[tuple[str, str, str], object], list[tuple[str, str, str, str, int]], int]:
    declared: dict[tuple[str, str, str], object] = {}
    misreads: list[tuple[str, str, str, str, int]] = []
    modules = 0
    for path in sorted(SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if not _body_module(source):
            continue
        modules += 1
        rel = path.relative_to(SRC).as_posix()
        found, wrong = _scan(source)
        declared |= {(rel, fn, field): how for (fn, field), how in found.items()}
        misreads += [(rel, *row) for row in wrong]
    return declared, misreads, modules


@pytest.fixture(scope="module")
def tree_scan() -> tuple[dict, list, int]:
    return _tree()


def test_every_body_boolean_is_pinned(tree_scan):
    """A boolean a door starts reading (or stops reading) fails here until the table says so, with
    what an omitted field means at that door."""
    declared, _, modules = tree_scan
    assert modules >= 80, f"only {modules} modules read a request body: the scan lost its root"
    added = {k: v for k, v in declared.items() if BODY_BOOLEANS.get(k, object()) != v}
    gone = sorted(set(BODY_BOOLEANS) - set(declared))
    assert not added and not gone, (
        "Read each body boolean with request_validation.bool_field (its default the field's safe "
        "value), require_bool or optional_bool, and pin it here with what an omitted field means.\n"
        f"  read, not pinned as read: {sorted(added.items())}\n  pinned, not read: {gone}"
    )


def test_no_body_boolean_is_read_by_truthiness(tree_scan):
    _, misreads, _ = tree_scan
    switches = {field for _, _, field in BODY_BOOLEANS} - _AMBIGUOUS
    wrong = sorted(
        row
        for row in set(misreads)
        if row[3] in {"bool()", "compare", "default"} or row[2] in switches
    )
    assert not wrong, (
        "A request body's boolean read by truthiness, where the text 'false' is a yes. Read it "
        "with request_validation.bool_field / require_bool / optional_bool:\n  "
        + "\n  ".join(f"{m}:{line} {fn} [{field}] {shape}" for m, fn, field, shape, line in wrong)
    )


def test_an_omitted_consent_or_widening_field_is_never_a_yes():
    assert not {
        k: v for k, v in BODY_BOOLEANS.items() if k[2] in _WIDENING and v is True
    }, "a field that grants consent or widens what runs must default to no, or be required"


@pytest.mark.parametrize(
    "shape",
    [
        "async def f(request):\n    body = await request.json()\n    return bool(body.get('flag'))",
        "async def f(request):\n    body = await json_object_body(request)\n"
        "    if body.get('flag'):\n        return 1",
        "async def f(request):\n    body = await json_object_body(request)\n"
        "    return body.get('flag') is not False",
        "async def f(request):\n    body = await json_object_body(request)\n"
        "    return body.get('flag', True)",
        "async def f(request):\n    body = await json_object_body(request)\n"
        "    x = body.get('flag')\n    return 1 if x else 0",
        "def helper(body):\n    return not body['flag']",
        "async def f(request):\n    body = await json_object_body(request)\n"
        "    for k in ('flag', 'other'):\n        if body[k]:\n            return 1",
    ],
)
def test_the_ban_sees_every_shape_of_a_misread(shape):
    """The ban's own control: each shape in which ``"false"`` reads as a yes is caught."""
    _, misreads = _scan(shape)
    assert any(field == "flag" for _, field, _, _ in misreads), shape


@pytest.mark.parametrize(
    ("shape", "how"),
    [
        (
            "async def f(request):\n    body = await json_object_body(request)\n"
            "    return bool_field(body, 'flag', default=False)",
            False,
        ),
        (
            "async def f(request):\n    body = await json_object_body(request)\n"
            "    return require_bool(body, 'flag')",
            "required",
        ),
        (
            "async def f(request):\n    body = await request.json()\n    v = body.get('flag')\n"
            "    if not isinstance(v, bool):\n        return refuse()\n    return 1 if v else 0",
            "checked",
        ),
        (
            "async def f(request):\n    body = await json_object_body(request)\n"
            "    for k in ('flag',):\n        optional_bool(body, k)",
            "optional",
        ),
    ],
)
def test_the_scan_declares_a_reader_and_a_checked_read(shape, how):
    """A read through a reader, or behind a hand-written refusal, is declared and is no misread."""
    declared, misreads = _scan(shape)
    assert declared.get(("f", "flag")) == how
    assert not misreads, misreads


# ── Stored settings ──────────────────────────────────────────────────────────────────────────

_LOADERS = {"from_dict", "_from_dict", "from_json", "from_row", "from_record", "from_stored"}
_STORED_READERS = {"strict_bool", "yes_or_no", "_guard_flag", "_expose_flag", "switched_off"}

#: Record loaders that still read a boolean by truthiness, and why each may: what they read is
#: written by the product itself, from real booleans. None of them is a switch the owner sets, or
#: an app's manifest (``apps/manifest.py`` reads every boolean through ``strict_bool`` and refuses
#: the install of one that is not true or false). A loader added to this set is a decision to
#: defend in review; one that leaves it must leave it here too.
LOADER_BUDGET = frozenset(
    {
        # Records the product writes from its own state.
        ("artifacts/models.py", "Artifact", "live_dirty"),
        ("artifacts/models.py", "Artifact", "readonly"),
        ("browse/plans.py", "BrowsePlan", "submits"),
        ("dashboard/side_state.py", "SideState", "open"),
        ("evals/ablation.py", "AblationComponent", "off_value"),
        ("evals/overlay.py", "ComponentOverlay", "off_value"),
        ("inbound/tokens.py", "SurfaceToken", "found"),
        ("learning/surfacing_events.py", "SurfacingEvent", "used"),
        ("memory_slots.py", "SlotLine", "tombstoned"),
        ("prompt_providers/base.py", "PromptVariable", "required"),
        ("proposals_contract.py", "Proposal", "editable"),
        ("selfqa/evidence.py", "Manifest", "passed"),
        ("tasks/models.py", "Task", "due_reminder"),
        ("tasks/models.py", "WorkflowTaskBinding", "managed"),
        ("triggers/models.py", "FireRecord", "acted_on"),
        ("triggers/models.py", "FireRecord", "dismissed"),
        ("triggers/models.py", "FireRecord", "incomplete"),
        ("triggers/models.py", "FireRecord", "mutated"),
        ("triggers/review.py", "ReviewCard", "count_is_floor"),
        ("triggers/review.py", "ReviewCard", "unannounced"),
        ("triggers/web_poll.py", "CheckRecord", "reported"),
        ("triggers/web_poll.py", "WatchState", "seeded"),
        ("workflows/human_input.py", "Ask", "rerun"),
        ("workflows/human_input.py", "Ask", "unattended_suppress"),
        ("workflows/human_input.py", "AskField", "required"),
        ("workflows/models.py", "DefMetadata", "a2a_published"),
        ("workflows/models.py", "DefMetadata", "guided"),
        ("workflows/models.py", "Failure", "recoverable"),
        ("workflows/models.py", "InputParam", "required"),
        ("workflows/models.py", "NodeInstance", "cached"),
        ("workflows/models.py", "WorkflowRun", "pinned"),
    }
)


def _loader_misreads(source: str) -> set[tuple[str, str]]:
    """``{(class, field)}`` for each boolean a record loader reads by truthiness: ``bool()`` of a
    read, a comparison of one with ``True``/``False``, or a ``.get(field, True|False)`` handed on
    raw. A read inside a strict reader is not one."""
    found: set[tuple[str, str]] = set()
    for cls in ast.walk(ast.parse(source)):
        if not isinstance(cls, ast.ClassDef):
            continue
        for fn in cls.body:
            if (
                not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                or fn.name not in _LOADERS
            ):
                continue
            params = {a.arg for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)}
            owners = params - {"cls", "self"}
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and len(node.targets) == 1:
                    value = node.value
                    if isinstance(value, ast.BoolOp):
                        value = value.values[0]
                    if (
                        isinstance(node.targets[0], ast.Name)
                        and isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Attribute)
                        and value.func.attr == "get"
                        and isinstance(value.func.value, ast.Name)
                        and value.func.value.id in params
                    ):
                        owners.add(node.targets[0].id)
            shielded = {
                id(sub)
                for node in ast.walk(fn)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _STORED_READERS
                for sub in ast.walk(node)
            }

            def key(node: ast.AST) -> str | None:
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in owners
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    return node.args[0].value
                if (
                    isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in owners
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)
                ):
                    return node.slice.value
                return None

            for node in ast.walk(fn):
                if id(node) in shielded:
                    continue
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id == "bool" and node.args:
                        found |= {(cls.name, k) for s in ast.walk(node.args[0]) if (k := key(s))}
                elif isinstance(node, ast.Compare):
                    sides = [node.left, *node.comparators]
                    if any(
                        isinstance(s, ast.Constant) and isinstance(s.value, bool) for s in sides
                    ):
                        found |= {(cls.name, k) for s in sides if (k := key(s))}
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and len(node.args) == 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, bool)
                    and (k := key(node))
                ):
                    found.add((cls.name, k))
    return found


def test_a_record_loader_reads_no_switch_by_truthiness():
    """A stored switch is read through ``safety_flags.strict_bool`` with its safe value; only the
    records in :data:`LOADER_BUDGET` may still read a boolean another way."""
    found = {
        (path.relative_to(SRC).as_posix(), cls, field)
        for path in sorted(SRC.rglob("*.py"))
        for cls, field in _loader_misreads(path.read_text(encoding="utf-8"))
    }
    assert found, "the loader scan found nothing at all: it no longer sees a loader"
    assert found == LOADER_BUDGET, (
        "Read a stored switch with safety_flags.strict_bool and its safe value "
        "(`absent=` for one that is on unless switched off).\n"
        f"  new: {sorted(found - LOADER_BUDGET)}\n  gone (drop from the budget): "
        f"{sorted(LOADER_BUDGET - found)}"
    )


@pytest.mark.parametrize(
    "shape",
    [
        "class R:\n    @classmethod\n    def from_dict(cls, d):\n"
        "        return cls(on=bool(d.get('on', True)))",
        "class R:\n    @classmethod\n    def from_dict(cls, d):\n"
        "        return cls(on=d.get('on', True))",
        "class R:\n    @classmethod\n    def from_dict(cls, d):\n"
        "        return cls(on=d.get('on') is not False)",
    ],
)
def test_the_loader_scan_sees_a_truthiness_read(shape):
    assert _loader_misreads(shape) == {("R", "on")}


def test_the_loader_scan_passes_a_strict_read():
    shape = (
        "class R:\n    @classmethod\n    def from_dict(cls, d):\n"
        "        return cls(on=strict_bool(d.get('on'), field='on', default=False))"
    )
    assert _loader_misreads(shape) == set()


def _schema_booleans() -> list[tuple[str, ...]]:
    from personalclaw.config.schema import JSON_SCHEMA

    leaves: list[tuple[str, ...]] = []

    def walk(node: dict[str, Any], path: tuple[str, ...]) -> None:
        kind = node.get("type")
        if "boolean" in (kind if isinstance(kind, list) else [kind]):
            leaves.append(path)
        for key, child in (node.get("properties") or {}).items():
            walk(child, (*path, key))
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            walk(extra, (*path, "*"))

    walk(JSON_SCHEMA, ())
    return leaves


_SCHEMA_BOOLEANS = _schema_booleans()


#: A boolean that is on only with another on (`SkillsConfig` switches refinement off without it).
_PREREQUISITES: dict[tuple[str, ...], dict[tuple[str, ...], object]] = {
    ("skills", "auto_refine_on_deviation"): {("skills", "auto_create_from_sessions"): True},
}


def _config_with(path: tuple[str, ...], value: object) -> dict[str, Any]:
    doc: dict[str, Any] = {}
    for at, setting in {**_PREREQUISITES.get(path, {}), path: value}.items():
        node = doc
        for key in at[:-1]:
            node = node.setdefault("an-agent" if key == "*" else key, {})
        node[at[-1]] = setting
    return doc


def _loaded(path: tuple[str, ...], cfg: Any) -> object:
    node: object = cfg.to_dict()
    for key in path:
        assert isinstance(node, dict), path
        node = node.get("an-agent" if key == "*" else key)
    return node


def test_the_config_schema_declares_booleans_at_every_depth():
    """Non-vacuity: the drive below covers the two-level fields and the deeper ones."""
    assert len(_SCHEMA_BOOLEANS) >= 100
    assert ("security", "egress", "allow_private") in _SCHEMA_BOOLEANS


@pytest.mark.parametrize("path", _SCHEMA_BOOLEANS, ids=".".join)
def test_a_config_boolean_written_as_false_text_reads_false(path):
    """Every boolean ``config.json`` holds: a text spelling of false reads as false at any depth
    (a switch the owner turned off stays off), a text spelling of true reads as the field's
    default (text never turns a surface on), and a real boolean is itself."""
    from personalclaw.config import validation
    from personalclaw.config.loader import AppConfig, config_dir, config_path

    config_dir()  # the test's own home, made on first ask

    def load(value: object) -> object:
        validation._STRIP_MEMO.clear()
        config_path().write_text(json.dumps(_config_with(path, value)), encoding="utf-8")
        return _loaded(path, AppConfig.load())

    default = load(None)
    assert load(True) is True
    assert load("false") is False
    assert load(" Off ") is False
    assert load(False) is False
    assert load("true") == default


# ── An MCP server's switch ───────────────────────────────────────────────────────────────────

#: Each module that reads an ``mcp.json`` server's ``disabled`` switch, and how many raw reads of it
#: it keeps: the toggle's disable path asks whether it is already the real ``true``, which is a
#: question about what to write, not a reading of the switch.
_MCP_SWITCH_READERS = {
    "mcp_client.py": 0,
    "mcp_discovery.py": 0,
    "agent.py": 0,
    "dashboard/handlers/mcp.py": 1,
    "providers/mcp_instances.py": 0,
}


@pytest.mark.parametrize("module, raw_reads", sorted(_MCP_SWITCH_READERS.items()))
def test_an_mcp_server_switch_is_read_one_way(module, raw_reads):
    source = (SRC / module).read_text(encoding="utf-8")
    assert "switched_off(" in source, f"{module} no longer reads a server's switch: re-pin it"
    assert source.count('.get("disabled")') == raw_reads, (
        f"{module} reads a server's `disabled` itself. Read it with mcp_status.switched_off, "
        "which reads the words as written and keeps a server it cannot read off."
    )
