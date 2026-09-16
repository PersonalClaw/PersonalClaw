"""WF2UNI-11 — the grounding-inputs WIRING inside `mcp_workflows.workflow_plan`.

The matcher's T4/T5 tiers, the brownfield synthesis, the entity-resolution preamble and topic
extraction each have their own suites (`test_workflows_planning_match` / `test_workflows_brownfield`
/ `test_workflows_preamble` / `test_workflows_settings`). What NO test exercised is the GLUE that
connects a *bound* memory service to those subsystems — the helpers `workflow_plan` calls at
mcp_workflows.py:764 (`_match_library`), :848 (`_prepend_grounding_preamble`), :872
(`_plan_topics`) and :1278 (`_codebase_context_for`). The atom was recorded `partial` only because
"no embedder/model was bound at audit time" — a runtime-binding gap, not a code gap: the glue
forwards *whatever* embedder/summarizer/resolver the running gateway wires. These tests inject a
fake `MemoryService` at the single resolution point (`_memory_service`) and prove the glue forwards
the live embedder/summarizer/resolver/threshold into the matcher and preamble, and degrades to
None/"" when nothing is wired.
"""

from __future__ import annotations

from typing import Any

import personalclaw.mcp_workflows as mw
from personalclaw.workflows.matcher import MATCH_THRESHOLD


class _FakeMemory:
    """The two capability flags the wiring branches on, plus the two bound methods it forwards."""

    def __init__(self, *, can_vector_search: bool = True, has_graph: bool = True) -> None:
        self.can_vector_search = can_vector_search
        self.has_graph = has_graph

    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    def resolve_entities(self, *a: Any, **k: Any) -> list[dict]:
        return []


# ── T4: the live embedder wiring ──


def test_live_embedder_returns_the_wired_embed(monkeypatch: Any) -> None:
    svc = _FakeMemory(can_vector_search=True)
    monkeypatch.setattr(mw, "_memory_service", lambda: svc)
    assert mw._live_embedder() == svc.embed


def test_live_embedder_is_none_when_vector_search_is_off(monkeypatch: Any) -> None:
    monkeypatch.setattr(mw, "_memory_service", lambda: _FakeMemory(can_vector_search=False))
    assert mw._live_embedder() is None


def test_live_embedder_is_none_with_no_service(monkeypatch: Any) -> None:
    monkeypatch.setattr(mw, "_memory_service", lambda: None)
    assert mw._live_embedder() is None


# ── T5: the live summarizer wiring ──


def test_live_summarizer_is_a_callable_when_a_service_is_wired(monkeypatch: Any) -> None:
    monkeypatch.setattr(mw, "_memory_service", lambda: _FakeMemory())
    assert callable(mw._live_summarizer())


def test_live_summarizer_is_none_with_no_service(monkeypatch: Any) -> None:
    monkeypatch.setattr(mw, "_memory_service", lambda: None)
    assert mw._live_summarizer() is None


# ── the entity resolver wiring (degraded → None) ──


def test_entity_resolver_returns_resolve_entities_when_a_graph_is_wired(monkeypatch: Any) -> None:
    svc = _FakeMemory(has_graph=True)
    monkeypatch.setattr(mw, "_memory_service", lambda: svc)
    assert mw._entity_resolver() == svc.resolve_entities


def test_entity_resolver_is_none_without_a_graph(monkeypatch: Any) -> None:
    monkeypatch.setattr(mw, "_memory_service", lambda: _FakeMemory(has_graph=False))
    assert mw._entity_resolver() is None


# ── the 0.62 tie-break threshold, read from live config ──


def test_match_threshold_is_the_documented_default() -> None:
    assert mw._match_threshold() == 0.62 == MATCH_THRESHOLD


# ── the matcher call actually FORWARDS the live injections (the load-bearing clause) ──


def test_match_library_forwards_live_embedder_summarizer_and_threshold(monkeypatch: Any) -> None:
    svc = _FakeMemory(can_vector_search=True)
    monkeypatch.setattr(mw, "_memory_service", lambda: svc)
    # Isolate the wiring from library loading: one sentinel profile is enough to reach the call.
    monkeypatch.setattr(mw, "_library_profiles", lambda **_k: [object()])

    captured: dict[str, Any] = {}

    def fake_match_template(
        goal: str,
        profiles: Any,
        *,
        shape: str = "",
        embedder: Any = None,
        summarizer: Any = None,
        threshold: Any = None,
    ) -> str:
        captured.update(
            embedder=embedder,
            summarizer=summarizer,
            threshold=threshold,
            shape=shape,
            n=len(list(profiles)),
        )
        return "MATCH"

    monkeypatch.setattr("personalclaw.workflows.matcher.match_template", fake_match_template)

    classified = type("C", (), {"shape": "make"})()
    result = mw._match_library("build a log parser", classified)

    assert result == "MATCH"
    assert captured["embedder"] == svc.embed  # T4 wired to the LIVE embedder
    assert callable(captured["summarizer"])  # T5 wired to the LIVE summarizer
    assert captured["threshold"] == 0.62  # the tie-break floor
    assert captured["shape"] == "make"  # the classified shape reaches the matcher
    assert captured["n"] >= 1


def test_match_library_is_none_when_no_profiles(monkeypatch: Any) -> None:
    monkeypatch.setattr(mw, "_library_profiles", lambda **_k: [])
    assert mw._match_library("anything", type("C", (), {"shape": ""})()) is None


# ── the preamble wiring: the live resolver reaches build_preamble_node, node lands first ──


def test_prepend_grounding_preamble_wires_the_live_resolver(monkeypatch: Any) -> None:
    svc = _FakeMemory(has_graph=True)
    monkeypatch.setattr(mw, "_memory_service", lambda: svc)

    captured: dict[str, Any] = {}

    def fake_build(goal: str, resolver: Any) -> dict:
        captured["resolver"] = resolver
        return {"kind": "transform", "id": "ground", "config": {}}

    monkeypatch.setattr("personalclaw.workflows.preamble.build_preamble_node", fake_build)

    root = {"kind": "action", "id": "work", "config": {}}
    result = mw._prepend_grounding_preamble("research Acme Corp", root)

    assert captured["resolver"] == svc.resolve_entities  # the LIVE resolver was wired in
    assert result != root  # the tree was rewritten
    assert "ground" in str(result)  # the entity-resolution node landed (prepend ran for real)


def test_prepend_grounding_preamble_returns_root_unchanged_when_no_node(monkeypatch: Any) -> None:
    monkeypatch.setattr(mw, "_memory_service", lambda: None)
    monkeypatch.setattr("personalclaw.workflows.preamble.build_preamble_node", lambda *a, **k: None)
    root = {"kind": "action", "id": "work", "config": {}}
    assert mw._prepend_grounding_preamble("nothing to ground", root) is root


# ── topic extraction feeding the grill ──


def test_plan_topics_is_wired_to_extract_topics(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "personalclaw.workflows.preamble.extract_topics", lambda goal: ["alpha", "beta"]
    )
    assert mw._plan_topics("anything") == ["alpha", "beta"]


# ── brownfield: codebase_context populated only when a project BINDS a workspace ──


def test_codebase_context_is_empty_for_no_project() -> None:
    assert mw._codebase_context_for("") == ""


def test_codebase_context_is_empty_when_the_project_binds_no_workspace(monkeypatch: Any) -> None:
    class _Store:
        def get_project(self, pid: str) -> Any:
            return type("P", (), {"workspace_dir": ""})()

    monkeypatch.setattr("personalclaw.tasks.hierarchy.HierarchyStore", _Store)
    assert mw._codebase_context_for("proj") == ""


def test_codebase_context_synthesizes_a_block_for_a_bound_workspace(
    tmp_path: Any, monkeypatch: Any
) -> None:
    (tmp_path / "README.md").write_text("# Demo\n\nA small log parser.\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("def main():\n    print('hi')\n", encoding="utf-8")
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "parser.py").write_text("def parse(line):\n    return line.split()\n", encoding="utf-8")

    class _Store:
        def get_project(self, pid: str) -> Any:
            return type("P", (), {"workspace_dir": str(tmp_path)})()

    monkeypatch.setattr("personalclaw.tasks.hierarchy.HierarchyStore", _Store)
    block = mw._codebase_context_for("proj")
    assert isinstance(block, str) and block != ""  # a populated brownfield block, from the tree
