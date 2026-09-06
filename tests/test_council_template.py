"""The `council` template's shape contract: independent fan-out, attributed fan-in.

`tests/test_workflows_bundled.py` already holds every bundled template to the library-wide
bar (it validates, it lints clean, its inputs are documented, its action nodes name
registered providers). None of that can tell whether a *council* is still a council, and
the two ways this template rots are both invisible to a validator:

* **The fan-out stops being independent.** Reshape the `parallel` into a `sequence`, or let
  one member's prompt bind another member's output, and the run still validates and still
  produces an answer — an answer three members converged on because they read each other.
  Three correlated reads presented as three independent ones is worse than one read, because
  the agreement looks like evidence.
* **The fan-in starts selecting instead of synthesizing.** Point the terminal binding at one
  member's output (which is exactly what `best-of-n` correctly does) and the template becomes
  a slower, more expensive best-of-N wearing a council's name. The attribution is the whole
  product: a decision the reader cannot trace back to the role that argued for it is one they
  cannot argue with.

So the properties asserted here are the ones the shape is FOR, read off the real
`workflows/bundled/council/workflow.json` rather than a fixture. Every detector also gets a
vacuity floor against `best-of-n` — the closest sibling and the one template that must FAIL
the attribution contract — because a detector that matched everything would satisfy each leg
below while proving nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows.bundled_defs import bundled_root
from personalclaw.workflows.models import CONTAINER_KINDS, Node, walk
from personalclaw.workflows.validator import dep_edges_for_root

TEMPLATE = "council"
SIBLING = "best-of-n"

#: The member node ids, in seat order. Named explicitly rather than derived from an id prefix:
#: the property under test is that THESE THREE exist and stay independent, and a prefix-derived
#: set would silently shrink to one seat — or to none — and then pass.
MEMBERS = ("member_1", "member_2", "member_3")

#: The fan-in's own nodes: the zero-token collector and the synthesizing call it feeds.
COLLECTOR = "positions"
SYNTHESIS = "synthesis"

#: `{{nodes.<id>…}}` — every node output a binding reaches.
_NODE_REF = re.compile(r"\{\{\s*nodes\.([A-Za-z0-9_-]+)")


def _raw(name: str) -> dict[str, Any]:
    return json.loads((bundled_root() / name / "workflow.json").read_text(encoding="utf-8"))


def _root(name: str) -> Node:
    return Node.from_dict(_raw(name)["root"])


def _by_id(name: str) -> dict[str, tuple[str, Node]]:
    return {node.id: (path, node) for path, node in walk(_root(name)) if node.id}


def _refs_in(obj: Any) -> set[str]:
    """Every node id a subtree's bindings read, at any nesting depth.

    Serialized and scanned rather than walked field by field: a binding can sit inside a
    prompt, a `with` map, a transform `expr` tree or a list of those, and a scan that knew
    only about `prompt` would miss the one that mattered.
    """
    return set(_NODE_REF.findall(json.dumps(obj)))


# ── the fan-out is genuinely parallel, and genuinely independent ──────────────


def test_the_members_are_siblings_under_one_parallel() -> None:
    """A `sequence` here would still validate and still answer — while serializing three calls
    that have no reason to wait for each other AND putting each member downstream of the last,
    which is where the independence quietly goes."""
    nodes = _by_id(TEMPLATE)
    parents = {path.rsplit(".children[", 1)[0] for path, _n in (nodes[m] for m in MEMBERS) if path}
    assert len(parents) == 1, f"the members are spread across {sorted(parents)}"
    container = dict(walk(_root(TEMPLATE)))[parents.pop()]
    assert container.kind.value == "parallel", (
        f"the members sit under a {container.kind.value!r} container: a council whose seats run "
        "in sequence is a relay, and each seat after the first can see the one before it"
    )


@pytest.mark.parametrize("member", MEMBERS)
def test_no_member_reads_another_member(member: str) -> None:
    """The premise of the fan-out. A member that binds a sibling's output is answering a
    different (and easier) question — "do you agree with this?" — and its answer stops being a
    second data point."""
    _path, node = _by_id(TEMPLATE)[member]
    read = _refs_in(node.to_dict())
    assert not read, (
        f"{member} reads {sorted(read)}: a member must see only the question and the shared "
        "context, or the council's three reads are one read with two echoes"
    )


@pytest.mark.parametrize("member", MEMBERS)
def test_each_member_argues_from_its_own_role(member: str) -> None:
    """Each seat binds its OWN role input. Two seats sharing one role blurb is the failure that
    reads as a working council and costs three calls to produce two opinions."""
    _path, node = _by_id(TEMPLATE)[member]
    own = f"inputs.{member}_role"
    text = json.dumps(node.to_dict())
    assert own in text, f"{member} never binds {own!r}"
    others = [f"inputs.{m}_role" for m in MEMBERS if m != member]
    borrowed = [ref for ref in others if ref in text]
    assert not borrowed, f"{member} also argues from {borrowed}"


def test_the_default_roles_actually_differ() -> None:
    """The defaults are what an unconfigured run gets, and they are the template's argument that
    three seats are worth three calls. Identical (or near-identical) blurbs make the fan-out a
    sampling run with extra steps."""
    inputs = _raw(TEMPLATE)["inputs"]
    defaults = [str(inputs[f"{m}_role"]["default"]).strip() for m in MEMBERS]
    assert all(defaults), "a seat ships with no default role"
    assert len(set(defaults)) == len(MEMBERS), "two seats ship the same default role"
    # Distinct STRINGS are cheap; distinct framings are the point. Overlap is measured on
    # content words so that the shared scaffolding ("the … — …") cannot pass for difference.
    words = [{w for w in re.findall(r"[a-z]{5,}", d.lower())} for d in defaults]
    for i, first in enumerate(words):
        for second in words[i + 1 :]:
            shared = first & second
            assert len(shared) <= 2, f"two default roles overlap on {sorted(shared)}"


# ── the fan-in merges and attributes, rather than selecting ───────────────────


def test_every_member_reaches_the_synthesis() -> None:
    """A dropped seat is the silent failure: the run still succeeds, the synthesis still reads
    confident, and one role's objection was simply never in the room. Traced through the
    collector, because that is the path the engine takes."""
    collected = _refs_in(_by_id(TEMPLATE)[COLLECTOR][1].to_dict())
    missing = [m for m in MEMBERS if m not in collected]
    assert not missing, f"{missing} never reach {COLLECTOR!r}"
    assert COLLECTOR in _refs_in(
        _by_id(TEMPLATE)[SYNTHESIS][1].to_dict()
    ), f"{SYNTHESIS!r} does not read {COLLECTOR!r}, so the collected slate feeds nothing"


def test_the_synthesis_is_ordered_after_every_member() -> None:
    """The ordering the engine can actually honour, asserted through the validator's own edge
    list rather than by reading the JSON's child order — an edge the engine cannot hold is a
    mid-run binding failure after every member has already paid for its call."""
    edges = dep_edges_for_root(_root(TEMPLATE))
    reachable = {SYNTHESIS, COLLECTOR}
    for _ in MEMBERS:  # one widening pass per hop is enough for this depth
        reachable |= {e.producer_id for e in edges if e.reader_id in reachable and e.producer_id}
    assert set(MEMBERS) <= reachable, f"{sorted(set(MEMBERS) - reachable)} are not upstream"
    for edge in edges:
        assert (
            edge.ordered
        ), f"{edge.reader_id} cannot be held for {edge.producer_id}: {edge.reason}"


def test_the_fan_in_attributes_every_position() -> None:
    """The discriminator against `best-of-n`. The synthesis must return a per-member record AND
    the dissent it did not follow: a decision that reports only its own conclusion has thrown
    away the disagreement, which is the only thing three seats bought over one."""
    schema = (_by_id(TEMPLATE)[SYNTHESIS][1].config or {}).get("schema") or {}
    assert schema.get("attributed") == "array", (
        "the synthesis returns no per-member attribution array, so the reader cannot tell which "
        "role the decision is standing on"
    )
    for field in ("disagreements", "dissent"):
        assert field in schema, f"the synthesis returns no {field!r}"
    prompt = str((_by_id(TEMPLATE)[SYNTHESIS][1].config or {}).get("prompt", ""))
    assert "attribut" in prompt.lower(), "the synthesis prompt never asks for attribution"


def test_nothing_selects_a_single_member_as_the_answer() -> None:
    """Selection is the sibling's contract, not this one's. A terminal binding pointing straight
    at one member's output turns a council into a best-of-N whose losing candidates were charged
    for and then discarded."""
    for path, node in walk(_root(TEMPLATE)):
        # Leaves only: `to_dict()` on a container serializes its whole subtree, so scanning one
        # would report every binding its children make as a read of its own.
        if node.id in MEMBERS or node.kind in CONTAINER_KINDS:
            continue
        read = _refs_in(node.to_dict())
        chosen = read & set(MEMBERS)
        assert not chosen or node.id == COLLECTOR, (
            f"{node.id or path} reads {sorted(chosen)} directly: every member's position must "
            f"arrive through {COLLECTOR!r}, where none of them can be dropped unnoticed"
        )


def test_the_sibling_fails_the_attribution_contract() -> None:
    """Vacuity floor. `best-of-n` is the nearest shape and it SELECTS — if it satisfied the
    attribution check above, that check is matching any template at all and the leg proves
    nothing about this one."""
    sibling_schemas = [(n.config or {}).get("schema") or {} for _p, n in walk(_root(SIBLING))]
    assert not any("attributed" in s for s in sibling_schemas), (
        f"{SIBLING} appears to attribute positions, so the attribution detector does not "
        "discriminate between selecting and synthesizing"
    )


# ── what the declared risk tier promises ─────────────────────────────────────


def test_the_council_only_ever_thinks() -> None:
    """`risk: low` and `capabilities: []` are the install-consent surface, and here they are a
    real claim: every working node is an `infer`, which is ONE bounded model call with no tools
    and no session. One `action` node would make the declaration a misrepresentation."""
    raw = _raw(TEMPLATE)
    kinds = {n.kind.value for _p, n in walk(_root(TEMPLATE))}
    assert "action" not in kinds and "stage" not in kinds, (
        f"the council dispatches {sorted(kinds)}: a seat with tools or a session can write, "
        "which is not what `risk: low` tells the user they are accepting"
    )
    assert raw["metadata"]["risk"] == "low"
    assert raw["metadata"]["capabilities"] == []


def test_the_cost_multiplier_is_declared() -> None:
    """Four reasoning-tier calls per run is the honest price, and a template that hides its
    multiplier is one a user discovers through their spend meter."""
    meta = _raw(TEMPLATE)["metadata"]
    assert meta.get("cost_tier") == "metered"
    assert str(meta.get("why_metered", "")).strip(), "metered with no stated reason"
    llm_nodes = [n for _p, n in walk(_root(TEMPLATE)) if n.kind.value == "infer"]
    assert len(llm_nodes) == len(MEMBERS) + 1, (
        f"{len(llm_nodes)} model calls, but `why_metered` describes one per member plus the "
        "synthesis — the stated price and the graph have to agree"
    )


def test_it_is_shipped_from_the_package_and_not_a_stray_file() -> None:
    """The wheel-versus-editable-install trap the library's own package-data test names: the
    directory has to be where `bundled_root()` looks, or an editable checkout passes every
    assertion above while `pip install personalclaw` ships no council at all."""
    assert (bundled_root() / TEMPLATE / "workflow.json").is_file()
    assert bundled_root().is_relative_to(Path(__file__).resolve().parents[1] / "src")
