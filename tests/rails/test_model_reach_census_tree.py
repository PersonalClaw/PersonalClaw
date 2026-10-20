"""The source-tree checks split from
``tests/test_model_reach_census.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index
from model_reach_census_shared import (
    ASKS,
    _functions,
    direct_embeds,
    media_generators,
    names_used,
    plain_pools,
    reads_their_input,
    registry_builds,
    scope_carrying_pools,
    stdio_dispatches,
)

import personalclaw

SRC = Path(personalclaw.__file__).parent


#: The functions that hand work to a model, each asking the one question (or, for the image and
#: video tools, the helper that asks it). Closed: a new seam is added here with its question.
SEAMS: dict[str, tuple[str, ...]] = {
    "embedding_providers/registry.py": ("embed_fn_for", "embed_many_fn_for"),
    "vector_memory.py": ("VectorMemoryStore._try_embed",),
    "providers/provider_bridge.py": ("metered", "resolve_provider_for_use_case", "_turn_failover"),
    "providers/image_input.py": ("resolve_image_reader",),
    "mcp_artifacts.py": (
        "_other_model_refusal",
        "_image_generate",
        "_video_generate",
        "regenerate_image_at_slug",
    ),
    "llm_helpers.py": ("_one_shot_completion",),
}


#: Where a provider's embedding is called directly: the seam that asks, and the owner's Settings
#: test of a model (a fixed word, outside any chat).
EMBEDS_DIRECTLY = frozenset({"embedding_providers/registry.py", "providers/model_test.py"})


#: Where a model provider is built from the model registry: the resolution seam (every build for
#: anything but a person's own turn passes ``metered``), the embedding seam, and the owner's
#: Settings test of a model.
BUILDS_MODELS = frozenset(
    {
        "providers/provider_bridge.py",
        "embedding_providers/registry.py",
        "providers/model_test.py",
    }
)


#: The readings that run as reading what the person gave the chat: an attachment read for its
#: text, from wherever its reading starts, and a screen they shared, described. Closed.
READS_THEIR_INPUT: dict[str, tuple[str, ...]] = {
    "dashboard/attachment_extract.py": ("AttachmentExtractor._begin", "AttachmentExtractor.get"),
    "dashboard/chat_runner.py": ("_describe_screen_frame",),
}


def _sources() -> dict[str, ast.Module]:
    """Every module of the package, parsed, from the suite's one source index."""
    return {str(path.relative_to(SRC)): tree for path, tree in source_index.package_trees()}


def test_every_worker_pool_carries_the_scope_of_the_work_it_is_handed():
    trees = _sources()
    found = {rel: lines for rel, tree in trees.items() if (lines := plain_pools(tree))}
    assert found == {}, f"a worker pool that drops which chat its work is for: {found}"
    carried = sum(scope_carrying_pools(tree) for tree in trees.values())
    assert carried >= 15, f"floor: the census read the tree ({carried} scope-carrying pools)"


def test_every_seam_that_hands_work_to_a_model_asks_the_one_question():
    trees = _sources()
    missing: list[str] = []
    for rel, names in SEAMS.items():
        functions = _functions(trees[rel])
        for name in names:
            assert name in functions, f"the seam {rel}::{name} no longer exists; update SEAMS"
            if not names_used(functions[name]) & ASKS:
                missing.append(f"{rel}::{name}")
    assert missing == [], f"a seam that hands work to a model without asking: {missing}"


def test_no_seam_asks_an_older_question_instead():
    """``writes_refused`` answers whether work may WRITE memory. A seam that asked it instead of
    the one question would let a chat's words reach a model the moment the two answers part."""
    trees = _sources()
    older = [
        f"{rel}::{name}"
        for rel, names in SEAMS.items()
        for name in names
        if "memory_writes.writes_refused" in names_used(_functions(trees[rel])[name])
    ]
    assert older == []


def test_nothing_calls_an_embedding_model_except_through_the_seam():
    trees = _sources()
    found = {rel: lines for rel, tree in trees.items() if (lines := direct_embeds(tree))}
    stray = {rel: lines for rel, lines in found.items() if rel not in EMBEDS_DIRECTLY}
    assert stray == {}, f"an embedding model called around the one question: {stray}"
    assert "embedding_providers/registry.py" in found, "floor: the census sees the seam's own calls"


def test_nothing_builds_a_model_provider_except_the_seams():
    trees = _sources()
    found = {rel: lines for rel, tree in trees.items() if (lines := registry_builds(tree))}
    stray = {rel: lines for rel, lines in found.items() if rel not in BUILDS_MODELS}
    assert stray == {}, f"a model provider built around the seams: {stray}"
    assert "providers/provider_bridge.py" in found, "floor: the census sees the resolution seam"


def test_every_function_that_makes_media_with_the_bound_model_asks():
    trees = _sources()
    makers = {rel: names for rel, tree in trees.items() if (names := media_generators(tree))}
    unasked = [
        f"{rel}::{name}"
        for rel, names in makers.items()
        for name in names
        if not names_used(_functions(trees[rel])[name]) & ASKS
    ]
    assert unasked == [], f"an image or video model reached without asking: {unasked}"
    assert len(makers.get("mcp_artifacts.py", [])) == 3, f"floor: {makers}"


def test_every_tool_server_an_agent_cli_runs_serves_each_call_as_its_chat():
    trees = _sources()
    found = {rel: names for rel, tree in trees.items() if (names := stdio_dispatches(tree))}
    assert found == {"mcp_core.py": ["_call_as_its_session"]}, found


def test_only_what_the_person_gives_the_chat_is_read_by_the_model_set_up_for_it():
    trees = _sources()
    found = {rel: names for rel, tree in trees.items() if (names := reads_their_input(tree))}
    assert found == {rel: list(names) for rel, names in READS_THEIR_INPUT.items()}, found
