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
from pathlib import Path

import pytest

import personalclaw
from personalclaw import memory_writes

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

#: What asking looks like: the one question, its raising form, the model the work stays on, or
#: the media tools' helper that asks it — each spelled as the seam spells it, so a name another
#: object happens to carry (a provider entry's ``own_model``) is not taken for asking.
ASKS = frozenset(
    {
        "memory_writes.model_may_read",
        "memory_writes.require_model",
        "memory_writes.own_model",
        "_other_model_refusal",
    }
)

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
    return {
        str(path.relative_to(SRC)): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(SRC.rglob("*.py"))
    }


# ── the detectors, each a function of one module's tree ─────────────────────────────────────


def plain_pools(tree: ast.Module) -> list[int]:
    """Lines that make a worker pool which does not carry the caller's context: a plain
    ``ThreadPoolExecutor`` however it is spelled or aliased, or a class built on it other than
    the one that carries the scope."""
    aliases = {"ThreadPoolExecutor"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "concurrent.futures":
            aliases |= {a.asname or a.name for a in node.names if a.name == "ThreadPoolExecutor"}
    found: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id in aliases) or (
                isinstance(func, ast.Attribute) and func.attr == "ThreadPoolExecutor"
            ):
                found.append(node.lineno)
        elif isinstance(node, ast.ClassDef) and node.name != "ScopeCarryingExecutor":
            for base in node.bases:
                named = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                if named in aliases or named == "ThreadPoolExecutor":
                    found.append(node.lineno)
    return found


def scope_carrying_pools(tree: ast.Module) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "ScopeCarryingExecutor"
    )


def _functions(tree: ast.Module) -> dict[str, ast.AST]:
    """Every function in the module by its qualified name (``Class.method`` for a method)."""
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}.{item.name}"] = item
    return out


def names_used(node: ast.AST) -> set[str]:
    """Every name the function reads or calls: each bare name, each attribute name, and each
    attribute with the name it is read from (``memory_writes.model_may_read``)."""
    used: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            used.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            used.add(sub.attr)
            if isinstance(sub.value, ast.Name):
                used.add(f"{sub.value.id}.{sub.attr}")
    return used


def direct_embeds(tree: ast.Module) -> list[int]:
    """Lines that call a provider's embedding (``.embed``, ``.embed_batch``, ``.get_embed_fn``)
    on anything but ``self``: a holder's own wrapper method calls through the seam."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"embed", "embed_batch", "get_embed_fn"}
        and not (isinstance(node.func.value, ast.Name) and node.func.value.id == "self")
    ]


def registry_builds(tree: ast.Module) -> list[int]:
    """Lines that build a model provider from the model registry: ``registry.build(...)`` or
    ``get_default_registry().build(...)``."""
    found: list[int] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "build"
        ):
            continue
        recv = node.func.value
        if (isinstance(recv, ast.Name) and recv.id == "registry") or (
            isinstance(recv, ast.Call)
            and isinstance(recv.func, ast.Name)
            and recv.func.id == "get_default_registry"
        ):
            found.append(node.lineno)
    return found


def media_generators(tree: ast.Module) -> list[str]:
    """Functions that resolve the bound image or video model and generate with it."""
    out: list[str] = []
    for name, fn in _functions(tree).items():
        used = names_used(fn)
        if used & {"active_image_gen", "active_video_gen"} and used & {"generate", "edit"}:
            out.append(name)
    return out


def reads_their_input(tree: ast.Module) -> list[str]:
    """Functions that run work as reading what the person gave the chat."""
    return [
        name
        for name, fn in _functions(tree).items()
        if "memory_writes.reading_their_input" in names_used(fn)
    ]


def stdio_dispatches(tree: ast.Module) -> list[str]:
    """The function each stdio tool server started in the module hands its calls to."""
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        named = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if named != "run_mcp_stdio_loop":
            continue
        dispatch = node.args[3] if len(node.args) > 3 else None
        for kw in node.keywords:
            if kw.arg == "call_tool_fn":
                dispatch = kw.value
        out.append(ast.unparse(dispatch) if dispatch is not None else "")
    return out


# ── the scope reaches every worker thread ───────────────────────────────────────────────────


def test_every_worker_pool_carries_the_scope_of_the_work_it_is_handed():
    trees = _sources()
    found = {rel: lines for rel, tree in trees.items() if (lines := plain_pools(tree))}
    assert found == {}, f"a worker pool that drops which chat its work is for: {found}"
    carried = sum(scope_carrying_pools(tree) for tree in trees.values())
    assert carried >= 15, f"floor: the census read the tree ({carried} scope-carrying pools)"


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


def test_every_tool_server_an_agent_cli_runs_serves_each_call_as_its_chat():
    trees = _sources()
    found = {rel: names for rel, tree in trees.items() if (names := stdio_dispatches(tree))}
    assert found == {"mcp_core.py": ["_call_as_its_session"]}, found


def test_the_server_census_finds_a_server_that_does_not():
    source = 'run_mcp_stdio_loop("other", "1.0.0", list_tools, call_tool)\n'
    assert stdio_dispatches(ast.parse(source)) == ["call_tool"]
    keyword = 'mcp_shared.run_mcp_stdio_loop("o", "1", lt, call_tool_fn=dispatch)\n'
    assert stdio_dispatches(ast.parse(keyword)) == ["dispatch"]


# ── the one exception: what the person gives the chat ───────────────────────────────────────


def test_only_what_the_person_gives_the_chat_is_read_by_the_model_set_up_for_it():
    trees = _sources()
    found = {rel: names for rel, tree in trees.items() if (names := reads_their_input(tree))}
    assert found == {rel: list(names) for rel, names in READS_THEIR_INPUT.items()}, found


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
