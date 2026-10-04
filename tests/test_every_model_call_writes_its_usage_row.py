"""Every model call PersonalClaw makes writes its usage row.

Settings → Usage sums the usage ledger (``usage/turns.jsonl``), so a call that writes no row is
spend it cannot show. Chat titles and tags, follow-up chips, suggestions, folder icons, memory
consolidation, the side chat, a hook's agent turn, the prompt optimizer, the eval and loop judges,
the knowledge nodes, a screenshot's description and every one-shot call whose caller named nobody
(digests among them) wrote none. The guarded ones reached only the model-call log, which Settings
→ Usage could state as "not included"; the rest left no record of their spend at all.

Two halves. The rail reads every place core consumes a model's stream (an ``async for`` over a
``.stream(...)`` or ``.complete(...)`` call, or over the same call read inside
``closing_stream(...)``, and every ``stream_and_collect(...)``) and fails for one whose function
writes no row. The behaviour half drives a chore, a one-shot call and three
judges through the real resolution seam, the real guard and a scripted model, and reads the row
each wrote, and that the model-call census no longer counts the call.

What the rail cannot see: a stream assigned to a name and iterated later. ``one_shot_completion``
writes its own row, so its callers are not scanned.

A turn that ends in an error has no ``EVENT_COMPLETE``: it sends ``EVENT_SPENT`` first, the usage of
the calls it made before the error, and a third half reads that every site consuming a stream
itself writes its row from that too (a collector does it for its callers). Without it the calls a
turn made before a dollar cap stopped it reached the spend meter and the model-call log, and never
Usage.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

# ── the rail ──────────────────────────────────────────────────────────────────────────────

#: A model's stream, consumed by ``async for``.
_STREAMS = {"stream", "complete"}
#: The helpers that collect a stream; each must be handed ``on_complete``, the row's writer.
_COLLECTORS = {"stream_and_collect"}
#: What writes a row: the ledger's own two doors, and the wrappers around them.
_WRITERS = {
    "recorder",
    "record_from_event",
    "_record_turn_usage",
    "_record_subagent_usage",
    "_usage_recorder",
}

#: Sites that consume a stream and rightly write no row of their own, and why.
EXEMPT = {
    ("llm_helpers.py", "stream_and_collect"): (
        "the collector itself: it hands the call's complete event to the caller's on_complete, "
        "which every collect is made to pass"
    ),
    ("dashboard/chat_utils.py", "stream_slash_command"): (
        "yields the chat turn's own events to the turn, which writes the turn's row"
    ),
    ("agents/native/runtime.py", "NativeAgentRuntime.stream"): (
        "the native loop's own inference: the turn's complete event carries its usage and its "
        "call ids to whoever writes the turn's row"
    ),
    ("sdk/provider_helpers.py", "BrandedCatalog._probe_completion"): (
        "a connection test that stops at the first event, before a call reports any usage"
    ),
}


def _name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _on_self(func: ast.expr) -> bool:
    """``self.stream(...)`` / ``super().stream(...)``: a provider composing its own methods."""
    if not isinstance(func, ast.Attribute):
        return False
    target = func.value
    if isinstance(target, ast.Name):
        return target.id == "self"
    return isinstance(target, ast.Call) and _name(target.func) == "super"


#: What a site wraps its stream in to write the row of a turn that ends in an error from its
#: ``EVENT_SPENT`` (``usage_ledger.spent_rows``): the stream inside it is still the site.
_SPENT_WRAPPERS = {"spent_rows"}
#: What a site reads its stream inside, so the stream is closed when the reading stops
#: (``turn_streams.closing_stream``): the stream it is handed is the site, and the ``async for``
#: inside reads it by the name the block binds.
_CLOSERS = {"closing_stream"}


def _read_stream(node: ast.AST) -> ast.Call | None:
    """The call whose stream *node* reads: an ``async for`` over a call, or a ``closing_stream``
    block over one. ``None`` for any other node."""
    if isinstance(node, ast.AsyncFor) and isinstance(node.iter, ast.Call):
        return node.iter
    if isinstance(node, ast.AsyncWith):
        for item in node.items:
            held = item.context_expr
            if (
                isinstance(held, ast.Call)
                and _name(held.func) in _CLOSERS
                and held.args
                and isinstance(held.args[0], ast.Call)
            ):
                return held.args[0]
    return None


def _sites(source: str):
    """Each place in *source* that consumes a model's stream: ``(qualified name, the functions
    around it, the collector call or None for an ``async for`` over the stream itself, whether
    the stream is wrapped in :data:`_SPENT_WRAPPERS`)``."""
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    for node in ast.walk(tree):
        read = _read_stream(node)
        if read is not None:
            stream = read
            wrapped = _name(stream.func) in _SPENT_WRAPPERS and bool(stream.args)
            if wrapped and isinstance(stream.args[0], ast.Call):
                stream = stream.args[0]
            func = stream.func
            if _name(func) not in _STREAMS or not isinstance(func, ast.Attribute):
                continue
            if _on_self(func):
                continue
            collect = None
        elif isinstance(node, ast.Call) and _name(node.func) in _COLLECTORS:
            collect, wrapped = node, False
        else:
            continue
        scopes: list[ast.AST] = []
        cursor: ast.AST = node
        while cursor in parents:
            cursor = parents[cursor]
            if isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scopes.append(cursor)
        qualname = ".".join(reversed([getattr(s, "name", "") for s in scopes])) or "<module>"
        yield qualname, [s for s in scopes if not isinstance(s, ast.ClassDef)], collect, wrapped


def unrecorded_calls(source: str) -> tuple[list[str], list[str]]:
    """``(every site, the sites that write no row)`` in *source*, each as its qualified name.

    A site writes its row when a function around it calls a writer (:data:`_WRITERS`), or, for
    a collector, when it passes ``on_complete``.
    """
    sites: list[str] = []
    missing: list[str] = []
    for qualname, functions, collect, _wrapped in _sites(source):
        sites.append(qualname)
        if collect is not None:
            written = any(kw.arg == "on_complete" for kw in collect.keywords)
        else:
            written = any(
                isinstance(call, ast.Call) and _name(call.func) in _WRITERS
                for scope in functions
                for call in ast.walk(scope)
            )
        if not written:
            missing.append(qualname)
    return sites, missing


def _census() -> tuple[dict[str, list[str]], list[tuple[str, str]]]:
    """Core's sites by file, and the ``(file, qualname)`` of each that writes no row."""
    everything: dict[str, list[str]] = {}
    unwritten: list[tuple[str, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(SRC).as_posix()
        sites, missing = unrecorded_calls(path.read_text(encoding="utf-8"))
        if sites:
            everything[rel] = sites
        unwritten.extend((rel, qualname) for qualname in missing)
    return everything, unwritten


def test_every_model_call_in_core_writes_its_usage_row():
    _sites, unwritten = _census()
    unexplained = [site for site in unwritten if site not in EXEMPT]
    assert not unexplained, (
        "these consume a model's stream and write no usage row, so Settings → Usage cannot show "
        "what they spend. Write one: `usage_ledger.recorder(provider, who)` called with the "
        "complete event, or `on_complete=` on the collector (`session.chore_usage`, "
        f"`chat_utils.chat_usage` name whose spend it is): {unexplained}"
    )


def test_every_exemption_still_names_a_site_that_writes_no_row():
    """An exemption that outlived its site would excuse the next one written there."""
    _sites, unwritten = _census()
    stale = sorted(set(EXEMPT) - set(unwritten))
    assert not stale, f"no longer a site without its row, drop the exemption: {stale}"


def test_the_rail_finds_the_sites_it_exists_for():
    """The floor: a scan that found nothing would pass for free."""
    sites, _unwritten = _census()
    found = {(rel, q) for rel, names in sites.items() for q in names}
    assert len(found) >= 20, found
    for known in (
        # The one-shot call every background chore is (``chores.run_chore``).
        ("llm_helpers.py", "_one_shot_completion._run"),
        ("dashboard/handlers/optimizer.py", "handle_optimize._optimize"),
        ("loop/judge.py", "_stream"),
        ("eval/judge.py", "LLMJudge.judge_turn"),
        ("gateway.py", "GatewayOrchestrator._run_heartbeat_task"),
    ):
        assert known in found, (known, sorted(found))


def test_the_rail_fails_a_stream_that_writes_nothing():
    _sites, missing = unrecorded_calls(
        "async def chore(client, prompt):\n"
        "    async for event in client.stream(prompt):\n"
        "        pass\n"
        "async def collect(client, prompt):\n"
        "    return await stream_and_collect(client, prompt)\n"
        "async def closed(client, prompt):\n"
        "    async with closing_stream(client.stream(prompt)) as events:\n"
        "        async for event in events:\n"
        "            pass\n"
    )
    assert sorted(missing) == ["chore", "closed", "collect"]


def test_the_rail_passes_a_stream_whose_function_writes_its_row():
    sites, missing = unrecorded_calls(
        "async def chore(client, prompt, who):\n"
        "    record = recorder(client, who)\n"
        "    async def inner():\n"
        "        async for event in client.stream(prompt):\n"
        "            record(event)\n"
        "    await inner()\n"
        "async def collect(client, prompt, who):\n"
        "    return await stream_and_collect(client, prompt, on_complete=recorder(client, who))\n"
        "class Provider:\n"
        "    async def send(self, prompt):\n"
        "        async for event in self.stream(prompt):\n"
        "            pass\n"
    )
    assert sites == ["chore.inner", "collect"]
    assert missing == []


# ── a turn that ends in an error ─────────────────────────────────────────────────────────

#: Sites that rightly never read ``EVENT_SPENT``, and why.
SPENT_EXEMPT = {
    ("dashboard/chat_utils.py", "stream_slash_command"): (
        "yields the turn's events, this one among them, to the turn, which writes the turn's row"
    ),
    ("sdk/provider_helpers.py", "BrandedCatalog._probe_completion"): (
        "a connection test that writes no row at all"
    ),
}


def spent_unread(source: str) -> list[str]:
    """The sites in *source* that consume a model's stream themselves and never read
    ``EVENT_SPENT``, neither themselves nor through :data:`_SPENT_WRAPPERS`: a turn there that
    ends in an error writes no row for the calls it made."""
    return [
        qualname
        for qualname, functions, collect, wrapped in _sites(source)
        if collect is None
        and not wrapped
        and not any(
            _name(node) == "EVENT_SPENT"
            for scope in functions
            for node in ast.walk(scope)
            if isinstance(node, (ast.Name, ast.Attribute))
        )
    ]


def _spent_census() -> list[tuple[str, str]]:
    unread: list[tuple[str, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(SRC).as_posix()
        unread.extend((rel, qualname) for qualname in spent_unread(path.read_text("utf-8")))
    return unread


def test_every_site_writes_its_row_for_a_turn_that_ends_in_an_error():
    unexplained = [site for site in _spent_census() if site not in SPENT_EXEMPT]
    assert not unexplained, (
        "these write a usage row from a turn's EVENT_COMPLETE and none from the EVENT_SPENT a "
        "turn ending in an error sends first, so the calls it made before the error are spend "
        f"Settings → Usage cannot show. Write the row from it too: {unexplained}"
    )


def test_every_spent_exemption_still_names_a_site_that_reads_none():
    stale = sorted(set(SPENT_EXEMPT) - set(_spent_census()))
    assert not stale, f"the site reads EVENT_SPENT now, drop the exemption: {stale}"


def test_the_spent_half_fails_a_site_that_reads_only_the_complete_event():
    assert spent_unread(
        "async def chore(client, prompt, record):\n"
        "    async for event in client.stream(prompt):\n"
        "        if event.kind == EVENT_COMPLETE:\n"
        "            record(event)\n"
        "async def kept(client, prompt, record):\n"
        "    async for event in client.stream(prompt):\n"
        "        if event.kind in (EVENT_COMPLETE, EVENT_SPENT):\n"
        "            record(event)\n"
        "async def wrapped(client, prompt, record):\n"
        "    async for event in spent_rows(client.stream(prompt), record):\n"
        "        if event.kind == EVENT_COMPLETE:\n"
        "            record(event)\n"
        "async def collect(client, prompt):\n"
        "    return await stream_and_collect(client, prompt, on_complete=print)\n"
    ) == ["chore"]


def test_a_stream_wrapped_to_write_its_spent_row_is_still_a_site():
    """Wrapping a stream in ``spent_rows`` must not hide it from the census: it is still a model's
    stream, and still writes its row or is named."""
    sites, missing = unrecorded_calls(
        "async def chore(client, prompt, record):\n"
        "    async for event in spent_rows(client.stream(prompt), record):\n"
        "        pass\n"
    )
    assert (sites, missing) == (["chore"], ["chore"])


# ── the rows, written ─────────────────────────────────────────────────────────────────────

ENTRY, MODEL = "rows-oai", "gpt-4o"
REF = f"{ENTRY}:{MODEL}"
TOKENS_IN, TOKENS_OUT = 12_000, 800


class _Scripted:
    """The one model every entry builds: it answers and reports its usage."""

    supports_tools = False

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def _answer(self):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="An answer")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=TOKENS_IN, output_tokens=TOKENS_OUT)

    def stream(self, message: str):
        return self._answer()

    def complete(self, messages: list[dict], **kw: Any):
        return self._answer()


@pytest.fixture
def scripted(monkeypatch) -> None:
    """One scripted entry, bound to every axis a chore or a judge resolves."""
    registry = ProviderRegistry()
    registry.register_type(
        ProviderCapability(
            type="scripted",
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=200_000,
        ),
        lambda **_kw: _Scripted(),
    )
    registry.register_entry(ProviderEntry(name=ENTRY, type="scripted", model=MODEL))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_active_models",
        lambda: {"chat": [REF], "background": [REF], "reasoning": [REF]},
    )


def _the_one_row() -> dict:
    """The one usage row the call wrote, naming the one guarded call it counts."""
    from personalclaw.guardrails.audit import _audit_path
    from personalclaw.usage_ledger import _iter_rows

    rows = _iter_rows()
    path = _audit_path()
    attempts = (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        if path.is_file()
        else []
    )
    assert len(rows) == 1, f"one model call, one usage row: {rows}"
    assert len(attempts) == 1, f"and one guarded call in the model-call log: {attempts}"
    assert rows[0]["audit_ids"] == [attempts[0]["audit_id"]], "the row names the call it counts"
    assert (rows[0]["input_tokens"], rows[0]["output_tokens"]) == (TOKENS_IN, TOKENS_OUT)
    return rows[0]


def _uncounted() -> int:
    """What Settings → Usage states as not included."""
    from personalclaw.config.loader import config_dir
    from personalclaw.routing.usage import fold_files

    return int(fold_files(home=config_dir())["uncounted"]["calls"])


@pytest.mark.asyncio
async def test_a_chats_title_is_the_chats_spend(scripted):
    from personalclaw.agents.defaults import LITE_AGENT_NAME
    from personalclaw.dashboard.chat_title import chat_chore

    text = await chat_chore(
        SimpleNamespace(key="chat-t", memory_mode="persistent"), "Title this chat."
    )

    assert text == "An answer"
    row = _the_one_row()
    assert (row["source"], row["session_key"], row["agent"]) == (
        "background",
        "dashboard:chat-t",
        LITE_AGENT_NAME,
    ), "a background chore, on the chat it was made for"
    assert (row["provider"], row["model"]) == (ENTRY, MODEL)
    assert _uncounted() == 0, "and the census no longer states it as not included"


@pytest.mark.asyncio
async def test_a_one_shot_call_that_names_nobody_is_background_spend(scripted):
    """A digest, a nav link, a reply check: one-shot calls whose callers named nobody."""
    from personalclaw.llm_helpers import one_shot_completion

    assert await one_shot_completion("Digest this.", use_case="background") == "An answer"

    row = _the_one_row()
    assert (row["source"], row["session_key"], row["agent"]) == ("background", "", "")
    assert _uncounted() == 0


@pytest.mark.asyncio
async def test_an_eval_judges_verdict_is_eval_spend(scripted):
    from personalclaw.eval.judge import LLMJudge
    from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

    judge = LLMJudge(lambda _key: resolve_provider_for_use_case("reasoning"))
    await judge.start()
    try:
        await judge.judge_turn("a scenario", "criteria", "a question", "an answer")
    finally:
        await judge.shutdown()

    row = _the_one_row()
    assert (row["source"], row["session_key"]) == ("eval", "eval_judge")
    assert _uncounted() == 0


@pytest.mark.asyncio
async def test_a_loop_judges_verdict_is_the_loops_spend(scripted):
    from personalclaw.loop import judge as judge_mod
    from personalclaw.loop.manager import loop_spend, usage_key

    finding = {"cycle": 1, "summary": "s"}
    await judge_mod.assess_cycle("a goal", "done when", finding, [], loop_id="l1")

    row = _the_one_row()
    # Under the key the loop's spend is read by, which is its worker's chat history key.
    assert (row["source"], row["session_key"]) == ("loop", usage_key("l1"))
    assert loop_spend("l1")["turns"] == 1, "the loop's own spend holds its judge"
    assert _uncounted() == 0


@pytest.mark.asyncio
async def test_a_stage_gates_verdict_is_the_loops_spend(scripted):
    from personalclaw.loop.gates import judge_verdict
    from personalclaw.loop.manager import loop_spend, usage_key

    assert await judge_verdict("PASS or FAIL?", loop_id="l2") == "An answer"

    row = _the_one_row()
    assert (row["source"], row["session_key"]) == ("loop", usage_key("l2"))
    assert loop_spend("l2")["turns"] == 1, "the loop's own spend holds its stage gate's verdict"
    assert _uncounted() == 0
