"""Every path that hands a chat's work to a model asks one question first, and none goes around it.

An Incognito or Temporary chat's words reach no model but the one its turn runs on. One function
answers whether the current work may hand what it carries to a given model
(``memory_writes.model_may_read``), and the promise holds only while two things stay true of the
whole tree, which is what these tests read:

* **The question is asked where models are reached.** The embedding functions, the guard every
  model built for anything but a person's own turn passes, an agent CLI built for such work, the
  image reader, the image and video tools, a turn's fallbacks and a one-shot call each ask it.
  Nothing invokes an embedding model or builds a model provider except those seams (and the
  owner's own Settings test of a model, which runs outside any chat).
* **The question can see which chat the work is for.** The answer reads a context variable, which
  a plain worker pool does not carry into its threads: a memory read bounded by a timeout once
  ran on one, and an Incognito chat's message reached the embedding model from its worker. Every
  worker pool is the one that carries it (``memory_writes.ScopeCarryingExecutor``). Nor does a
  context variable cross into another process: the tool server an agent CLI runs serves each call
  as the chat it serves, which the gateway names (``mcp_core._call_as_its_session``).

One answer differs on purpose, and only where these tests name it: what the person gives the chat
themselves in a form its model cannot read (a file they attach, read for its text; a screen they
share, described) is read by the model they set up for it (``memory_writes.reading_their_input``).

Each census below is shown to find what it looks for (a positive control) before its zero is
trusted, and to read the real tree (a floor).
"""

from __future__ import annotations

import ast
import asyncio

import pytest
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

from personalclaw import memory_writes

# ── the detectors, each a function of one module's tree ─────────────────────────────────────


# ── the scope reaches every worker thread ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "source",
    [
        "import concurrent.futures\nconcurrent.futures.ThreadPoolExecutor(max_workers=1)\n",
        "import concurrent.futures as cf\nwith cf.ThreadPoolExecutor() as pool:\n    pass\n",
        "from concurrent.futures import ThreadPoolExecutor as Pool\nPool(max_workers=2)\n",
        (
            "from concurrent.futures import ThreadPoolExecutor\n"
            "class Mine(ThreadPoolExecutor):\n    pass\n"
        ),
    ],
)
def test_the_pool_census_finds_a_plain_pool_however_it_is_spelled(source):
    assert plain_pools(ast.parse(source)), source


def test_the_pool_census_passes_the_one_that_carries_the_scope():
    source = "from personalclaw import memory_writes\nmemory_writes.ScopeCarryingExecutor(1)\n"
    assert plain_pools(ast.parse(source)) == []
    assert scope_carrying_pools(ast.parse(source)) == 1


def test_a_worker_of_the_pool_knows_the_chat_its_work_is_for():
    """The behaviour the census stands in for, and the defect it pins: a plain pool's worker
    did not know, and the scope-carrying pool's does."""
    import concurrent.futures

    with memory_writes.derived_from("dashboard:chat-7-1700000000", memory_mode="incognito"):
        assert not memory_writes.model_may_read("cloud-embed:embed-v1")
        with memory_writes.ScopeCarryingExecutor(max_workers=1) as pool:
            assert not pool.submit(memory_writes.model_may_read, "cloud-embed:embed-v1").result()
        plain = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            assert plain.submit(
                memory_writes.model_may_read, "cloud-embed:embed-v1"
            ).result(), (
                "control: a plain pool's worker cannot see the chat, which is why none is used"
            )
        finally:
            plain.shutdown(wait=True)


# ── the seams ask the one question ──────────────────────────────────────────────────────────


def test_the_seam_census_finds_a_seam_that_does_not_ask():
    asks = ast.parse(
        "def seam(ref):\n    if not memory_writes.model_may_read(ref):\n        return\n"
    )
    silent = ast.parse("def seam(ref):\n    return provider.embed(ref)\n")
    # A provider entry's own model is a name another object carries: reading it is not asking.
    lookalike = ast.parse("def seam(fallback):\n    return fallback.own_model\n")
    assert names_used(_functions(asks)["seam"]) & ASKS
    assert not names_used(_functions(silent)["seam"]) & ASKS
    assert not names_used(_functions(lookalike)["seam"]) & ASKS


# ── nothing reaches a model around them ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("detector", "source"),
    [
        (direct_embeds, "vec = provider.embed('their words', model='m')\n"),
        (direct_embeds, "fn = native.get_embed_fn('m')\n"),
        (registry_builds, "p = get_default_registry().build('cloud', model='m')\n"),
        (registry_builds, "p = registry.build('cloud')\n"),
    ],
)
def test_the_reach_census_finds_a_call_around_the_seams(detector, source):
    assert detector(ast.parse(source)), source


def test_the_media_census_finds_a_maker_that_does_not_ask():
    source = (
        "def make(prompt):\n"
        "    provider, model = active_image_gen()\n"
        "    return provider.generate(prompt, model=model)\n"
    )
    tree = ast.parse(source)
    assert media_generators(tree) == ["make"]
    assert not names_used(_functions(tree)["make"]) & ASKS


# ── the scope reaches the tool process an agent CLI runs ────────────────────────────────────


def test_the_server_census_finds_a_server_that_does_not():
    source = 'run_mcp_stdio_loop("other", "1.0.0", list_tools, call_tool)\n'
    assert stdio_dispatches(ast.parse(source)) == ["call_tool"]
    keyword = 'mcp_shared.run_mcp_stdio_loop("o", "1", lt, call_tool_fn=dispatch)\n'
    assert stdio_dispatches(ast.parse(keyword)) == ["dispatch"]


# ── the one exception: what the person gives the chat ───────────────────────────────────────


def test_the_exception_census_finds_a_reading_that_takes_it():
    source = "def read(path):\n    with memory_writes.reading_their_input():\n        pass\n"
    assert reads_their_input(ast.parse(source)) == ["read"]


def test_reading_what_the_person_gave_changes_only_which_model_may_read_it():
    async def drive() -> list[tuple[bool, bool, bool, object]]:
        def seen() -> tuple[bool, bool, bool, object]:
            return (
                memory_writes.model_may_read("vision:big"),
                memory_writes.writes_refused(),
                memory_writes.blocks_background_models("dashboard:chat-7-1700000000"),
                memory_writes.own_model(),
            )

        with memory_writes.derived_from("dashboard:chat-7-1700000000", memory_mode="incognito"):
            memory_writes.answered_by("here:tiny")
            out = [seen()]
            with memory_writes.reading_their_input():
                out.append(seen())
                task = asyncio.create_task(asyncio.sleep(0))
            await task
            out.append(task.get_context().run(seen))
            out.append(seen())
        with memory_writes.reading_their_input():
            out.append(seen())
        return out

    assert asyncio.run(drive()) == [
        (False, True, True, "here:tiny"),
        (True, True, True, None),
        (True, True, True, None),
        (False, True, True, "here:tiny"),
        (True, False, False, None),
    ]


# ── the one question's answer ───────────────────────────────────────────────────────────────


def test_the_answer_outside_a_restricted_chats_work_is_always_yes():
    assert memory_writes.model_may_read("any-entry:any-model")
    assert memory_writes.own_model() is None
    with memory_writes.derived_from("dashboard:chat-8-1700000100", memory_mode="persistent"):
        assert memory_writes.model_may_read("any-entry:any-model")
        assert memory_writes.own_model() is None


def test_inside_one_only_the_model_its_turn_named_is_allowed():
    with memory_writes.derived_from("dashboard:chat-7-1700000000", memory_mode="temporary"):
        assert memory_writes.own_model() == ""
        assert not memory_writes.model_may_read("here:tiny"), "nothing before the turn names one"
        memory_writes.answered_by("here:tiny")
        assert memory_writes.own_model() == "here:tiny"
        assert memory_writes.model_may_read("here:tiny")
        assert not memory_writes.model_may_read("relay:swift")
        assert not memory_writes.model_may_read(""), "a function that names no model"
        with pytest.raises(memory_writes.OtherModelRefused) as refused:
            memory_writes.require_model("relay:swift")
    assert str(refused.value) == (
        "This chat is Temporary, so nothing from it is sent to any model but the one it runs on: "
        "relay:swift was not asked."
    )
    assert memory_writes.model_may_read("relay:swift"), "the turn's model does not outlive it"


def test_work_started_before_the_turn_named_its_model_hands_nothing_on():
    async def drive() -> tuple[bool, bool]:
        with memory_writes.derived_from("dashboard:chat-7-1700000000", memory_mode="incognito"):
            early = asyncio.get_running_loop().create_future()

            async def started_early() -> None:
                await early
                return None

            task = asyncio.create_task(started_early())
            memory_writes.answered_by("here:tiny")
            early.set_result(None)
            await task
            seen_by_early = task.get_context().run(memory_writes.model_may_read, "here:tiny")
            return seen_by_early, memory_writes.model_may_read("here:tiny")

    early, after = asyncio.run(drive())
    assert (early, after) == (False, True)
