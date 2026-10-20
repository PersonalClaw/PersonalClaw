"""Exhaustive sub-resource GET census for #2940.

The original regression test named ten handlers.  That cannot detect the eleventh route
that repeats the same ghost-parent/real-empty ambiguity.  This rail instead AST-walks every
Python file under :mod:`personalclaw`, selecting both path families that can hide the bug:

* a GET with a non-terminal ``{param}`` segment; and
* a deep GET whose terminal ``{param}`` may itself name a parent-scoped collection.

Every selected route must be directly covered by ``test_parent_resource_validation.py`` or be
listed, with the reason it needs no pair, in :data:`SUBRESOURCE_READS`, a site baseline.  The
coverage map is checked for stale paths and handler drift, and the list for new and stale routes,
each by name.  Static inspection follows ``test_api_manifest_drift.py``: booting the dashboard
just to walk its live route table has security-sensitive startup side effects.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

import pytest
import site_baseline
import source_index
from rails_repo import REPO

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
_PARENT_TEST = REPO / "tests" / "test_parent_resource_validation.py"

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
    "/api/workflows/{name}/automations": "api_def_automations",
    "/api/workflows/{name}/ledger": "api_def_ledger",
    "/api/workflows/{name}/trajectory": "api_template_trajectory",
    "/api/workflows/{name}/versions/diff": "api_def_version_diff",
    "/api/workflows/{name}/versions/{version}": "api_def_version_detail",
}

#: The selected routes no direct pair covers, one line each with the resolver or the endpoint
#: contract that rules a ghost parent out, in the per-site format of ``tests/site_baseline.py``,
#: each site ``GET <canonical path>``. A new route the census selects fails by name until a direct
#: ghost/real pair covers it or it has its own line, with ``added`` and a reason, which the rails
#: job lists in its summary; a route that is gone or now covered has its line deleted. Most deep
#: terminal-parameter routes are plain detail or key reads; the two that list a collection for
#: their parent (autonudge and ephemeral skills) are covered above. It replaced the exact counts
#: ``len(nonterminal) == 90``, ``len(deep_terminal) == 19`` and ``len(selected) == 109``.
SUBRESOURCE_READS = "tests/rails/baselines/subresource-reads.sites.jsonl"

#: The vacuity floor: the walk reads the package's literal GET routes, about 400 of them.
_GET_ROUTE_FLOOR = 300


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


def _routes_of(tree: ast.AST, routes: dict[str, str]) -> None:
    """Add every literal GET route *tree* registers to *routes*, canonical path to handler."""
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


def _get_routes(planted: Iterable[ast.AST] = ()) -> dict[str, str]:
    """Return every literal GET route in all of ``src/personalclaw`` by AST, and in each
    *planted* module (the positive control's)."""
    routes: dict[str, str] = {}
    for tree in [*(tree for _, tree in source_index.package_trees()), *planted]:
        _routes_of(tree, routes)
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


def _census(routes: dict[str, str]) -> tuple[dict[str, str], set[str], set[str]]:
    nonterminal = {path for path in routes if _has_nonterminal_param(path)}
    deep_terminal = {path for path in routes if _is_deep_terminal_param(path)}
    selected = {path: routes[path] for path in nonterminal | deep_terminal}
    return selected, nonterminal, deep_terminal


def _sites(selected: Iterable[str]) -> list[str]:
    """The baseline's sites: each selected route no direct pair covers, as ``GET <path>``."""
    return [f"GET {path}" for path in sorted(set(selected) - set(PARENT_READ_COVERED))]


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


def test_every_selected_route_is_covered_or_listed_with_its_reason():
    """Every route the census selects is driven by a direct ghost/real pair
    (:data:`PARENT_READ_COVERED`) or listed in :data:`SUBRESOURCE_READS` with the reason it needs
    none. A route neither holds fails by name, and a listed route that is gone, or that a direct
    pair now covers, fails on its stale line."""
    routes = _get_routes()
    selected, nonterminal, deep_terminal = _census(routes)
    assert len(routes) > _GET_ROUTE_FLOOR, (
        f"read only {len(routes)} GET routes (floor {_GET_ROUTE_FLOOR}): the walk is broken, "
        "not the code"
    )
    assert nonterminal and deep_terminal, "a route family selected nothing: the matcher is broken"
    stale = sorted(set(PARENT_READ_COVERED) - set(selected))
    assert not stale, f"covered routes the census no longer selects: {stale}"
    site_baseline.check(_sites(selected), SUBRESOURCE_READS)


#: A module that registers a route of each family, and two the census does not select.
_PLANTED = """\
def setup(app):
    app.router.add_get("/api/planted/{id}/children", api_planted_children)
    app.router.add_route("GET", "/api/planted/{id}/stream", api_planted_stream)
    app.router.add_get("/api/planted/deep/detail/{id}", api_planted_detail)
    app.router.add_get("/api/planted/{id}", api_planted_one)
    app.router.add_post("/api/planted/{id}/children", api_planted_add)
"""
_PLANTED_SITES = (
    "GET /api/planted/{id}/children",
    "GET /api/planted/{id}/stream",
    "GET /api/planted/deep/detail/{id}",
)


def test_a_planted_route_fails_by_name_and_a_gone_or_covered_one_by_its_line(
    monkeypatch: pytest.MonkeyPatch,
):
    """The positive control, on the real list: the tree's routes with a planted module's fail,
    naming its non-terminal and deep terminal-parameter GETs, and neither its shallow detail read
    nor its POST is a site. The tree's sites less one, as if that route were gone, fail on its
    stale line, and so does a listed route a direct pair now covers. No run writes the list."""
    listed = (REPO / SUBRESOURCE_READS).read_text(encoding="utf-8")
    routes = _get_routes()
    selected, _, _ = _census(_get_routes([ast.parse(_PLANTED)]))
    with pytest.raises(AssertionError) as failed:
        site_baseline.check(_sites(selected), SUBRESOURCE_READS, None, prune=False)
    message = str(failed.value)
    for site in _PLANTED_SITES:
        assert f"Site {site} is not listed" in message, message
    assert message.count("is not listed") == len(_PLANTED_SITES), message
    assert "is listed in" not in message, message
    gone, *rest = _sites(_census(routes)[0])
    with pytest.raises(AssertionError, match=f"Site {re.escape(gone)} is listed in"):
        site_baseline.check(rest, SUBRESOURCE_READS, None, prune=False)
    path = gone.removeprefix("GET ")
    monkeypatch.setitem(PARENT_READ_COVERED, path, routes[path])
    with pytest.raises(AssertionError, match=f"Site {re.escape(gone)} is listed in") as covered:
        site_baseline.check(_sites(_census(routes)[0]), SUBRESOURCE_READS, None, prune=False)
    assert str(covered.value).count("is listed in") == 1, covered.value
    assert (REPO / SUBRESOURCE_READS).read_text(encoding="utf-8") == listed


def test_covered_routes_still_target_directly_tested_handlers():
    selected, _, _ = _census(_get_routes())
    named = _directly_named_handlers()
    drift = {
        path: {"expected": handler, "registered": selected.get(path)}
        for path, handler in PARENT_READ_COVERED.items()
        if selected.get(path) != handler
    }
    assert not drift, f"covered route handler drift: {drift}"
    untested = sorted(set(PARENT_READ_COVERED.values()) - named)
    assert not untested, f"coverage map names handlers absent from the direct suite: {untested}"
