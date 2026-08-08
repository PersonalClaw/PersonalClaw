"""The grounding preamble (UP-R14, WF2UNI-11).

The preamble's whole job is to resolve identity deterministically and degrade honestly when it
cannot, so these tests assert both the resolved path (the injected resolver's entities land in the
first node) and the degraded path (no resolver → a name-only node that still carries the guard).
The topic split is asserted separately because it feeds a different subsystem — the grill's lookup
channels — and a merged assertion would hide which half broke.
"""

from personalclaw.workflows.preamble import (
    IDENTITY_GUARD,
    NO_PATTERN_MATCH_PROHIBITION,
    build_preamble_node,
    extract_topics,
    prepend_preamble,
    resolve_entities,
)


def test_topics_are_the_goals_content_nouns():
    topics = extract_topics("research the japan trip logistics and budget")
    assert "japan" in topics and "logistics" in topics and "budget" in topics
    assert "the" not in topics and "and" not in topics


def test_topic_extraction_is_deterministic():
    goal = "compare the vector databases for the search feature"
    assert extract_topics(goal) == extract_topics(goal)


def test_a_resolver_resolves_entities_deterministically():
    def resolver(_text):
        return [{"id": "e1", "name": "Ana", "entity_type": "person", "aliases": []}]

    entities, degraded = resolve_entities("book a table for Ana", resolver)
    assert entities and entities[0]["name"] == "Ana"
    assert not degraded


def test_no_resolver_degrades():
    entities, degraded = resolve_entities("book a table for Ana", None)
    assert entities == []
    assert degraded, "no graph wired is the degraded fallback the node must record"


def test_a_resolver_that_finds_nothing_degrades():
    entities, degraded = resolve_entities("book a table for Ana", lambda _t: [])
    assert entities == []
    assert degraded


def test_a_broken_resolver_degrades_rather_than_raising():
    def boom(_text):
        raise RuntimeError("graph down")

    entities, degraded = resolve_entities("anything", boom)
    assert entities == []
    assert degraded


def test_the_preamble_node_is_a_first_class_transform_carrying_the_guard():
    def resolver(_text):
        return [{"id": "e1", "name": "Acme", "entity_type": "company", "aliases": []}]

    node = build_preamble_node("analyze Acme's earnings", resolver)
    assert node is not None
    assert node["kind"] == "transform"
    assert node["id"] == "ground"
    payload = node["config"]["expr"]
    assert payload["resolved_entities"][0]["name"] == "Acme"
    assert payload["degraded"] is False
    assert payload["guard"] == IDENTITY_GUARD
    # A company is an entity-heavy domain → the do-not-pattern-match prohibition rides along.
    assert payload["prohibition"] == NO_PATTERN_MATCH_PROHIBITION


def test_a_degraded_node_still_emits_with_the_guard_and_flag():
    node = build_preamble_node("analyze Acme's earnings", None)
    assert node is not None
    payload = node["config"]["expr"]
    assert payload["degraded"] is True
    assert payload["guard"] == IDENTITY_GUARD
    assert payload["prohibition"] == NO_PATTERN_MATCH_PROHIBITION
    assert payload["topics"], "a degraded resolution still extracts topics for the grill"


def test_no_node_when_there_is_nothing_to_ground():
    # A goal with no extractable topic and no resolver has nothing to resolve or look up.
    assert build_preamble_node("", None) is None


def test_prepend_puts_the_node_first_in_a_sequence():
    root = {"kind": "sequence", "id": "root", "children": [{"kind": "stage", "id": "work"}]}
    node = build_preamble_node("analyze Acme", lambda _t: [])
    out = prepend_preamble(root, node)
    assert out["children"][0]["id"] == "ground"
    assert out["children"][1]["id"] == "work"
    assert root["children"][0]["id"] == "work", "the caller's tree is not mutated"


def test_prepend_wraps_a_non_sequence_root():
    root = {"kind": "stage", "id": "solo"}
    node = build_preamble_node("analyze Acme", lambda _t: [])
    out = prepend_preamble(root, node)
    assert out["kind"] == "sequence"
    assert out["children"][0]["id"] == "ground"
    assert out["children"][1]["id"] == "solo"


def test_the_preamble_node_binds_and_validates_in_a_real_tree():
    """The emitted transform must be a legal node the engine accepts, or the preamble breaks every
    plan it grounds. A whole-value literal `expr` passes the transform's `WF_MISSING_EXPR` check."""
    from personalclaw.workflows.models import Node
    from personalclaw.workflows.validator import validate_node_tree

    root = {
        "kind": "sequence",
        "id": "root",
        "children": [{"kind": "stage", "id": "work", "config": {"prompt": "do it"}}],
    }
    node = build_preamble_node("analyze Acme's earnings", lambda _t: [])
    grounded = prepend_preamble(root, node)
    result = validate_node_tree(Node.from_dict(grounded))
    assert not [i for i in result.issues if i.code == "WF_MISSING_EXPR"]
