"""`rich-ingest` reads what its lenses produced — through the calls the engine actually makes.

The template shipped `| default([])` on seven reads: its judge gate's prompt and the item list of
each of its five persist fan-outs. `[]` is not a pipe literal, so every resolution raised. The
gate failed its prompt after the five lens stages had spent their tokens, and each fan-out's
`tick._resolve_items` swallowed the error as "not resolvable yet", so it would never have started.
Library-wide, the validator now refuses such a call (`test_workflows_bundled.py`,
`TestEveryPipeCallParses`); this file holds THIS template to what its author meant: a lens that
extracted items has every one of them persisted, and a lens whose `items` came back null has
nothing to persist — an empty fan-out, which is complete, not one that waits forever.

Read off the template as the gateway serves it (`bundled_defs.read_template`: macros expanded,
blocks resolved), never a raw `json.load`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from personalclaw.workflows.bindings import BindingContext, resolve
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.models import Node, walk
from personalclaw.workflows.tick import _resolve_items

TEMPLATE = "rich-ingest"

#: Each persist fan-out and the lens it reads. Named rather than derived from an id prefix, so a
#: renamed fan-out fails here instead of silently dropping out of the check.
FAN_OUTS = {
    "store-decisions": "lens-decisions",
    "store-references": "lens-references",
    "store-facts": "lens-facts",
    "store-summary": "lens-summary",
    "store-tasks": "lens-tasks",
}

GATE = "grounded-in-transcript"


def _nodes() -> dict[str, Node]:
    loaded = read_template(TEMPLATE)
    assert loaded is not None, f"{TEMPLATE} does not load"
    return {node.id: node for _path, node in walk(loaded.root) if node.id}


def _items(lens: str) -> list[dict[str, Any]]:
    return [
        {"title": f"{lens} one", "body": "said in the meeting", "evidence": "line 3"},
        {"title": f"{lens} two", "body": "also said", "evidence": "line 9"},
    ]


def _ctx(null_lenses: frozenset[str] = frozenset()) -> BindingContext:
    outputs = {
        lens: {"items": None if lens in null_lenses else _items(lens)} for lens in FAN_OUTS.values()
    }
    return BindingContext(
        inputs={"transcript": "…", "source_label": "arch-review"}, node_outputs=outputs
    )


def test_the_template_has_the_fan_outs_and_the_gate_this_file_checks() -> None:
    nodes = _nodes()
    assert set(FAN_OUTS) | set(FAN_OUTS.values()) | {GATE} <= set(nodes)


@pytest.mark.parametrize("fan_out", sorted(FAN_OUTS))
def test_each_fan_out_persists_every_item_its_lens_extracted(fan_out: str) -> None:
    items = _resolve_items(_nodes()[fan_out], _ctx())
    assert items == _items(FAN_OUTS[fan_out]), (
        f"{fan_out}'s item list resolved to {items!r} — `None` is the frontier's 'not resolvable "
        "yet', so the fan-out would never start"
    )


@pytest.mark.parametrize("fan_out", sorted(FAN_OUTS))
def test_a_lens_that_came_back_null_is_an_empty_fan_out_not_a_stall(fan_out: str) -> None:
    lens = FAN_OUTS[fan_out]
    assert _resolve_items(_nodes()[fan_out], _ctx(frozenset({lens}))) == []


def test_the_judge_gate_is_shown_both_lenses_it_judges() -> None:
    prompt = resolve(_nodes()[GATE].config["prompt"], _ctx())
    assert json.dumps(_items("lens-decisions"), ensure_ascii=False) in prompt
    assert json.dumps(_items("lens-facts"), ensure_ascii=False) in prompt


def test_the_judge_gate_is_told_a_lens_found_nothing_rather_than_failing() -> None:
    prompt = resolve(_nodes()[GATE].config["prompt"], _ctx(frozenset({"lens-decisions"})))
    assert "Extracted decisions:\n[]\n" in prompt
    assert json.dumps(_items("lens-facts"), ensure_ascii=False) in prompt
