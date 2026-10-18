"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_request_boolean_census.py``

Its checks:
``tests/rails/test_request_boolean_census_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

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
    ("dashboard/chat_regenerate.py", "api_chat_session_edit_resend", "again"): False,
    ("dashboard/chat_regenerate.py", "api_chat_session_edit_resend", "rewind"): False,
    ("dashboard/chat_tags.py", "api_chat_tag_create", "status"): False,
    ("dashboard/chat_tags.py", "api_chat_tag_update", "status"): "optional",
    ("dashboard/handlers/autonudge.py", "api_autonudge_update", "active"): None,
    ("dashboard/handlers/core.py", "api_project_trust", "trusted"): "required",
    ("dashboard/handlers/core.py", "api_sel_rotate", "archive"): True,
    (
        "dashboard/handlers/external_access.py",
        "api_external_access_client",
        "persistent_sessions",
    ): False,
    (
        "dashboard/handlers/external_access.py",
        "api_external_access_client_persistent_sessions",
        "persistent_sessions",
    ): "required",
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
    (
        "dashboard/handlers/loop_routes.py",
        "api_loop_agent_cli_self_approval",
        "allowed",
    ): "required",
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
    ("dashboard/handlers/trigger_consent.py", "_grant_for_save", "enabled"): False,
    ("dashboard/handlers/trigger_runs.py", "_run_store", "dry_run"): False,
    ("dashboard/handlers/trigger_runs.py", "api_trigger_answer", "answer"): "required",
    ("dashboard/handlers/triggers.py", "_create_schedule", "catch_up"): False,
    ("dashboard/handlers/triggers.py", "_create_schedule", "enabled"): True,
    ("dashboard/handlers/triggers.py", "_create_schedule", "failure_dedupe"): False,
    ("dashboard/handlers/triggers.py", "_create_schedule", "silent"): False,
    ("dashboard/handlers/triggers.py", "_create_schedule", "strict_schedule"): False,
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


def _scan(
    source: str | ast.Module,
) -> tuple[dict[tuple[str, str], object], list[tuple[str, str, str, int]]]:
    """``(declared, misreads)`` for one module.

    *declared* maps ``(function, field)`` to what an omitted field means there, for every boolean
    the function reads through a reader or refuses by hand. *misreads* lists ``(function, field,
    shape, line)`` for every read of a body field by truthiness: ``bool()``, ``not``, an
    ``if``/``while``/ternary/``assert``/comprehension test, an ``and``/``or`` operand, a comparison
    with ``True``/``False``, or a ``.get(field, True|False)`` default. A name bound straight from a
    raw read counts as the read, unless a hand-written refusal checks it in between.
    """
    tree = source if isinstance(source, ast.Module) else ast.parse(source)
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


_LOADERS = {"from_dict", "_from_dict", "from_json", "from_row", "from_record", "from_stored"}


_STORED_READERS = {"strict_bool", "yes_or_no", "_guard_flag", "_expose_flag", "switched_off"}


def _loader_misreads(source: str | ast.Module) -> set[tuple[str, str]]:
    """``{(class, field)}`` for each boolean a record loader reads by truthiness: ``bool()`` of a
    read, a comparison of one with ``True``/``False``, or a ``.get(field, True|False)`` handed on
    raw. A read inside a strict reader is not one."""
    found: set[tuple[str, str]] = set()
    tree = source if isinstance(source, ast.Module) else ast.parse(source)
    for cls in ast.walk(tree):
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
