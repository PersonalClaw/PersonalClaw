"""Exhaustive sub-resource GET census for #2940.

The original regression test named ten handlers.  That cannot detect the eleventh route
that repeats the same ghost-parent/real-empty ambiguity.  This rail instead AST-walks every
Python file under :mod:`personalclaw`, selecting both path families that can hide the bug:

* a GET with a non-terminal ``{param}`` segment; and
* a deep GET whose terminal ``{param}`` may itself name a parent-scoped collection.

Every selected route must be directly covered by ``test_parent_resource_validation.py`` or
have a written exclusion below.  Both maps are checked for stale paths and handler drift.
Static inspection follows ``test_api_manifest_drift.py``: booting the dashboard just to walk
its live route table has security-sensitive startup side effects.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import personalclaw
from personalclaw.manifest_meta import canonical_route

_ADD_VERBS = {
    "add_get",
    "add_post",
    "add_put",
    "add_delete",
    "add_patch",
    "add_head",
    "add_options",
}
_PARAM = re.compile(r"\{[^}]+\}")
_SRC_ROOT = Path(personalclaw.__file__).parent
_PARENT_TEST = Path(__file__).with_name("test_parent_resource_validation.py")

# A route is covered only when this path->handler pair is driven directly by the parent
# validation suite.  Keeping the handler makes a registration retarget fail loudly.
PARENT_READ_COVERED = {
    "/api/apps/{name}/uninstall-preview": "api_app_uninstall_preview",
    "/api/artifacts/{slug}/versions": "api_artifact_versions",
    "/api/autonudge/session/{session_name}": "api_autonudge_get",
    "/api/feedback/target/{kind}/{id}": "api_feedback_target",
    "/api/knowledge/entities/by-name/{name}/items": "get_entity_items",
    "/api/knowledge/entities/by-name/{name}/related": "get_entity_related",
    "/api/knowledge/items/{id}/relations": "list_item_relations",
    "/api/memory/entities/{entity_id}/backlinks": "api_memory_entity_backlinks",
    "/api/sessions/{id}/agents": "api_session_agents_list",
    "/api/skills/ephemeral/{session}": "api_ephemeral_skills_list",
    "/api/tasks/{task_id}/comments": "api_tasks_comments_get",
    "/api/triggers/{id}/history": "api_trigger_history",
    "/api/workflows/{name}/ledger": "api_def_ledger",
    "/api/workflows/{name}/trajectory": "api_template_trajectory",
    "/api/workflows/{name}/versions/diff": "api_def_version_diff",
}

# MANIFEST_EXCLUDE-shaped on purpose: each candidate is named once with the reason it
# does not need another ghost-parent regression pair.  Repeated prose would hide the
# important distinction, so each value names the resolver or the endpoint contract.
PARENT_READ_EXCLUDE = {
    "/api/agent-providers/{id}/agents": (
        "registry rejects unknown ids; the built-in runtime intentionally has no catalog"
    ),
    "/api/apps/{name}/agent-run/{run_id}": (
        "run lookup is app-owned and rejects absent or cross-app run ids"
    ),
    "/api/apps/{name}/config": "resolves the installed app manifest before reading config",
    "/api/artifacts/{slug}/events": "provider.get resolves the artifact before listing events",
    "/api/artifacts/{slug}/extract": "provider.get resolves the artifact before extraction",
    "/api/artifacts/{slug}/model": "the artifact resolver rejects an unknown slug before decoding",
    "/api/artifacts/{slug}/raw": "provider.get resolves the artifact before serving bytes",
    "/api/artifacts/{slug}/versions/{version}": (
        "provider.get resolves the exact artifact version; this is a child detail read"
    ),
    "/api/chat/sessions/{session}/export": "_read_transcript rejects a missing session",
    "/api/chat/sessions/{session}/map": "full_session_messages rejects a missing session",
    "/api/chat/sessions/{session}/organize": "resolve_session rejects a missing session",
    "/api/chat/sessions/{session}/plan-session": "_resolve rejects a missing live chat session",
    "/api/chat/sessions/{session}/rewind": "_resolve_session rejects a missing live chat session",
    "/api/chat/sessions/{session}/tool-result/{rid}": (
        "the exact session/result key must resolve in the raw-result store"
    ),
    "/api/dashboard/views/{view_id}/tiles/refresh": (
        "tile_refresh rejects a missing view or tile with tile_not_found"
    ),
    "/api/durability/history/{root}/timeline": (
        "_history_root validates the root against the durable-root registry"
    ),
    "/api/knowledge/collections/{id}/items": (
        "get_collection resolves the collection before resolving its items"
    ),
    "/api/knowledge/entities/{id}/graph": "the graph must contain the entity node",
    "/api/knowledge/intents/{id}/outcomes": "the intent store resolves the intent first",
    "/api/knowledge/items/{id}/annotations": "get_item resolves the item before annotations",
    "/api/knowledge/items/{id}/content": "get_item resolves the item before returning content",
    "/api/knowledge/items/{id}/duplicates": "get_item resolves the item before duplicate search",
    "/api/knowledge/items/{id}/extracted": "get_item resolves the item before extracted content",
    "/api/knowledge/items/{id}/file": "_serve_item_path rejects an item without a stored file",
    "/api/knowledge/items/{id}/graph": "get_item resolves the item before projecting its graph",
    "/api/knowledge/items/{id}/ingest/stream": (
        "SSE event transport, not an empty collection read; the id selects a per-item progress feed"
    ),
    "/api/knowledge/items/{id}/intents": "get_item resolves the item before listing outcomes",
    "/api/knowledge/items/{id}/related": (
        "the related-items handler explicitly distinguishes unknown items from no close neighbours"
    ),
    "/api/knowledge/items/{id}/sections": "get_item resolves the item before sectioning",
    "/api/knowledge/items/{id}/staleness": "staleness_for rejects an unknown item",
    "/api/knowledge/items/{id}/thumbnail": "_serve_item_path rejects an item without a thumbnail",
    "/api/loops/{id}/design/tokens": "loop store resolves the design loop before token projection",
    "/api/loops/{id}/plan-session": "loop store resolves the loop before nullable plan state",
    "/api/loops/{id}/report": "loop store resolves the loop before reading empty-on-missing files",
    "/api/loops/{id}/stream": "_loop_view resolves the loop before opening its SSE stream",
    "/api/model-providers/{name}/models": "model-provider registry resolves the entry first",
    "/api/model-providers/{name}/search": "model-provider registry resolves the entry first",
    "/api/model-providers/{name}/show": "model-provider registry resolves the entry first",
    "/api/models/downloads/{id}/stream": "download registry resolves the job before SSE setup",
    "/api/models/embedding/reindex/{id}/stream": "reindex registry resolves the SSE job",
    "/api/models/local/{provider}/health": "local-model registry rejects an unknown provider",
    "/api/models/local/{provider}/search": "local-model registry rejects an unknown provider",
    "/api/models/sidecar/{provider}/install/status": (
        "sidecar registry rejects providers without an install definition"
    ),
    "/api/models/use-cases/{use_case}/settings": "VALID_USE_CASES validates this enum-like key",
    "/api/projects/{project_id}/export": "project store resolves the project before export",
    "/api/projects/{project_id}/linked": "project store resolves the project before linked work",
    "/api/projects/{project_id}/work": "project store resolves the project before board projection",
    "/api/providers/{name}/config": "provider registry resolves the extension before config",
    "/api/providers/{name}/instances": "provider registry resolves the extension before instances",
    "/api/providers/{name}/instances/{id}": (
        "provider registry and then the exact instance are both resolved; this is child detail"
    ),
    "/api/providers/{name}/schema": "provider registry resolves the extension before schema",
    "/api/rooms/{room_id}/export": (
        "rooms.store.require_room resolves the room before any transcript read, so a ghost "
        "room is room_not_found rather than an empty export (asserted in test_rooms_api.py)"
    ),
    "/api/sessions/{id}/agents/{agent_id}": (
        "read_result requires the exact session/agent result; this is child detail"
    ),
    "/api/sessions/{id}/agents/{agent_id}/stream": (
        "SSE may attach before the child result exists; this is not a collection read"
    ),
    "/api/skills/{name}/files": "skill-root resolver rejects unknown skills before browsing",
    "/api/triggers/{id}/history/{run_id}": (
        "the run store resolves the exact trigger/run pair; this is child detail"
    ),
    "/api/voice/profiles/{id}/audio": "require_profile resolves the profile before serving audio",
    "/api/workflows/runs/{run_id}/continuations": "run store rejects unknown runs first",
    "/api/workflows/runs/{run_id}/deliverable": "service.run_deliverable rejects an unknown run",
    "/api/workflows/runs/{run_id}/drop": "service.drop_status rejects an unknown run",
    "/api/workflows/runs/{run_id}/events": "run store resolves the run before SSE setup",
    "/api/workflows/runs/{run_id}/introspect": "service.introspect rejects an unknown run",
    "/api/workflows/runs/{run_id}/ledger-rails": "service.ledger_rails rejects an unknown run",
    "/api/workflows/runs/{run_id}/nodes/{node_id}/inspect": (
        "service.inspect_node resolves the run and node; this is child detail"
    ),
    "/api/workflows/runs/{run_id}/outbox": "service.outbox rejects an unknown run",
    "/api/workflows/runs/{run_id}/outputs/{node_id}": (
        "service.output resolves the run and node; this is child detail"
    ),
    "/api/workflows/runs/{run_id}/review": "review_findings rejects an unknown run",
    "/api/workflows/runs/{run_id}/steering": "guard and service resolve the run first",
    "/api/workflows/runs/{run_id}/workspace": "service.workspace_review rejects an unknown run",
    "/api/workflows/{name}/versions": "service.get_def resolves the workflow first",
    # Deep terminal-parameter controls.  Fourteen are ordinary detail/key reads; the two
    # collection-for-parent exceptions (autonudge and ephemeral skills) are covered above.
    "/api/agent-marketplace/agents/{name}": "plain detail; marketplace.get rejects absence",
    "/api/agents/detail/{name}": "plain detail; agent-file resolver rejects absence",
    "/api/chat/sessions/{session}": "plain detail; live-or-persisted resolver rejects absence",
    "/api/dashboard/views/{view_id}": "plain view detail; get_view rejects unknown ids",
    "/api/desktop/capabilities/{cap}": "enum-key detail; CAPABILITIES rejects unknown names",
    "/api/doctor/crash/{filename}": "plain crash-artifact detail; read_crash rejects absence",
    "/api/evals/studies/{study_id}": "plain study detail; study_view rejects absence",
    "/api/knowledge/items/{id}": "plain item detail; get_item rejects absence",
    "/api/learning/proposals/{id}": "plain proposal detail; proposal store rejects absence",
    "/api/session/archive/{name}": "plain archive-file detail; file read rejects absence",
    "/api/skills/proposals/{id}": "plain proposal detail; proposal store rejects absence",
    "/api/voice/profiles/{id}": "plain profile detail; require_profile rejects absence",
    "/api/workflows/runs/{run_id}": "plain run detail; service.status rejects absence",
    "/api/ws/terminal/{session_id}": (
        "WebSocket creation; session_id is a new client-selected key, not a parent"
    ),
}


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _get_routes() -> dict[str, str]:
    """Return every literal GET route in all of ``src/personalclaw`` by AST."""
    routes: dict[str, str] = {}
    for py in sorted(_SRC_ROOT.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            verb = node.func.attr
            path: str | None = None
            handler = ""
            if verb == "add_get" and node.args:
                path = _literal(node.args[0])
                handler = _name(node.args[1]) if len(node.args) > 1 else ""
            elif verb == "add_route" and len(node.args) >= 3:
                method = _literal(node.args[0])
                if method and method.upper() == "GET":
                    path = _literal(node.args[1])
                    handler = _name(node.args[2])
            elif verb in _ADD_VERBS:
                continue
            if path is None or not path.startswith("/api"):
                continue
            canonical = canonical_route(path)
            previous = routes.setdefault(canonical, handler)
            assert previous == handler, (
                f"GET {canonical} registers more than once with different handlers: "
                f"{previous!r}, {handler!r}"
            )
    return routes


def _parts(path: str) -> list[str]:
    return [part for part in path.split("?", 1)[0].split("/") if part]


def _has_nonterminal_param(path: str) -> bool:
    parts = _parts(path)
    return any(_PARAM.search(part) and index < len(parts) - 1 for index, part in enumerate(parts))


def _is_deep_terminal_param(path: str) -> bool:
    parts = _parts(path)
    if not parts or not _PARAM.search(parts[-1]) or _has_nonterminal_param(path):
        return False
    literal_prefix = sum(not _PARAM.search(part) for part in parts[:-1])
    return literal_prefix >= 3


def _census() -> tuple[dict[str, str], set[str], set[str]]:
    routes = _get_routes()
    nonterminal = {path for path in routes if _has_nonterminal_param(path)}
    deep_terminal = {path for path in routes if _is_deep_terminal_param(path)}
    selected = {path: routes[path] for path in nonterminal | deep_terminal}
    return selected, nonterminal, deep_terminal


def _directly_named_handlers() -> set[str]:
    """Handler symbols named by the direct regression suite, including parametrized names."""
    tree = ast.parse(_PARENT_TEST.read_text(encoding="utf-8"))
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names.update(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    return names


def test_all_src_get_census_is_fully_adjudicated():
    selected, nonterminal, deep_terminal = _census()
    assert len(nonterminal) == 83
    assert len(deep_terminal) == 16
    assert len(selected) == 99

    covered = set(PARENT_READ_COVERED)
    excluded = set(PARENT_READ_EXCLUDE)
    assert not covered & excluded, f"routes both covered and excluded: {sorted(covered & excluded)}"
    assert set(selected) == covered | excluded, (
        "GET census drift. Add a direct ghost/real pair or a written exclusion. "
        f"unadjudicated={sorted(set(selected) - covered - excluded)}; "
        f"stale={sorted((covered | excluded) - set(selected))}"
    )
    empty_reasons = sorted(
        path for path, reason in PARENT_READ_EXCLUDE.items() if not reason.strip()
    )
    assert not empty_reasons, f"reasonless exclusions: {empty_reasons}"


def test_covered_routes_still_target_directly_tested_handlers():
    selected, _, _ = _census()
    named = _directly_named_handlers()
    drift = {
        path: {"expected": handler, "registered": selected.get(path)}
        for path, handler in PARENT_READ_COVERED.items()
        if selected.get(path) != handler
    }
    assert not drift, f"covered route handler drift: {drift}"
    untested = sorted(set(PARENT_READ_COVERED.values()) - named)
    assert not untested, f"coverage map names handlers absent from the direct suite: {untested}"
