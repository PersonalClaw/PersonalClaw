"""Every model call PersonalClaw makes writes its usage row.

Settings → Usage sums the usage ledger (``usage/turns.jsonl``), so a call that writes no row is
spend it cannot show. Chat titles and tags, follow-up chips, suggestions, folder icons, memory
consolidation, the side chat, a hook's agent turn, the prompt optimizer, the eval and loop judges,
the knowledge nodes, a screenshot's description and every one-shot call whose caller named nobody
(digests among them) wrote none. The guarded ones reached only the model-call log, which Settings
→ Usage could state as "not included"; the rest left no record of their spend at all.

Two halves. The rail reads every place core consumes a model's stream (an ``async for`` over a
``.stream(...)`` or ``.complete(...)`` call, and every ``stream_and_collect(...)``) and fails for
one whose function writes no row. The behaviour half drives a chore, a one-shot call and three
judges through the real resolution seam, the real guard and a scripted model, and reads the row
each wrote, and that the model-call census no longer counts the call.

What the rail cannot see: a stream assigned to a name and iterated later. ``one_shot_completion``
writes its own row, so its callers are not scanned.
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
_COLLECTORS = {"stream_and_collect", "stream_and_collect_json"}
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


def unrecorded_calls(source: str) -> tuple[list[str], list[str]]:
    """``(every site, the sites that write no row)`` in *source*, each as its qualified name.

    A site writes its row when a function around it calls a writer (:data:`_WRITERS`), or, for
    a collector, when it passes ``on_complete``.
    """
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    sites: list[str] = []
    missing: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFor) and isinstance(node.iter, ast.Call):
            func = node.iter.func
            if _name(func) not in _STREAMS or not isinstance(func, ast.Attribute):
                continue
            if _on_self(func):
                continue
            collect = None
        elif isinstance(node, ast.Call) and _name(node.func) in _COLLECTORS:
            collect = node
        else:
            continue
        scopes: list[ast.AST] = []
        cursor: ast.AST = node
        while cursor in parents:
            cursor = parents[cursor]
            if isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scopes.append(cursor)
        qualname = ".".join(reversed([getattr(s, "name", "") for s in scopes])) or "<module>"
        sites.append(qualname)
        if collect is not None:
            written = any(kw.arg == "on_complete" for kw in collect.keywords)
        else:
            functions = [s for s in scopes if not isinstance(s, ast.ClassDef)]
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
    assert len(found) >= 25, found
    for known in (
        ("dashboard/chat_title.py", "_stream_background_prompt"),
        ("suggestions.py", "generate_suggestions._stream"),
        ("loop/judge.py", "_stream"),
        ("eval/judge.py", "LLMJudge.judge_turn"),
        ("history.py", "HistoryConsolidator._call_llm"),
    ):
        assert known in found, (known, sorted(found))


def test_the_rail_fails_a_stream_that_writes_nothing():
    _sites, missing = unrecorded_calls(
        "async def chore(client, prompt):\n"
        "    async for event in client.stream(prompt):\n"
        "        pass\n"
        "async def collect(client, prompt):\n"
        "    return await stream_and_collect(client, prompt)\n"
    )
    assert missing == ["chore", "collect"]


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


class _BackgroundSessions:
    """The ``SessionManager`` surface a chore uses: the real lite agent on the Background axis."""

    async def get_or_create(self, key: str, agent: str | None = None, **_kw: Any):
        from personalclaw.agents.defaults import LITE_AGENT_NAME
        from personalclaw.providers import provider_bridge

        runtime = provider_bridge._build_native_runtime(
            use_case="chat",
            session_key=key,
            agent=agent or LITE_AGENT_NAME,
            model_override=None,
            cwd=None,
            model_axis="background",
        )
        await runtime.start()
        return runtime, True, False

    def release(self, key: str) -> None:
        return None


@pytest.mark.asyncio
async def test_a_chats_title_is_the_chats_spend(scripted):
    from personalclaw.agents.defaults import LITE_AGENT_NAME
    from personalclaw.dashboard.chat_title import _stream_background_prompt
    from personalclaw.session import chore_usage

    state = SimpleNamespace(sessions=_BackgroundSessions())
    text = await _stream_background_prompt(
        state, "Title this chat.", usage=chore_usage("dashboard:chat-t")
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
