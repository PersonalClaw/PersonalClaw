"""Tests for the deterministic scripted fake model provider (PHF-7).

Every test that constructs a provider runs against a ``tmp_path`` home and a ``tmp_path``
script; the real ``~/.personalclaw`` is never read, written, or created. The two
real-home refusal tests additionally poison ``config_dir`` so that a regression which
reached for it — and therefore ``mkdir``'d the real home — fails loudly instead of
quietly touching it.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
)
from personalclaw.llm.scripted import (
    HOME_ENV_VAR,
    SCRIPT_ENV_VAR,
    ScriptedProvider,
    ScriptedProviderNotEnabled,
    ScriptedProviderRefused,
    ScriptedScriptError,
    resolve_script_path,
)

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

# Networking + vendor-SDK modules that must never appear in this provider's import
# graph. ``socket``/``ssl``/``urllib``/``http`` are in the list on purpose: the delta
# assertion below isolates this module from the package ``__init__``s, which already
# load those, so including them makes the assertion strictly stronger.
FORBIDDEN_IMPORTS = frozenset(
    {
        "aiohttp",
        "anthropic",
        "boto3",
        "botocore",
        "grpc",
        "http",
        "httpcore",
        "httpx",
        "openai",
        "requests",
        "socket",
        "ssl",
        "urllib",
        "urllib3",
        "websocket",
        "websockets",
    }
)


# ── Script builders ───────────────────────────────────────────────────


def _write_script(tmp_path: Path, script: dict[str, Any], name: str = "script.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(script), encoding="utf-8")
    return path


def _text_script() -> dict[str, Any]:
    return {
        "version": 1,
        "context_usage_pct": 12.5,
        "turns": [
            {
                "chunks": ["Hello from ", "the scripted provider."],
                "usage": {
                    "input_tokens": 42,
                    "output_tokens": 7,
                    "cache_creation_tokens": 3,
                    "cache_read_tokens": 11,
                },
                "stop_reason": "end_turn",
                "duration_ms": 250,
            }
        ],
    }


def _tool_script() -> dict[str, Any]:
    return {
        "version": 1,
        "turns": [
            {
                "thinking": ["I should read the file."],
                "text": "Let me check that file.",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "read_file",
                        "input": {"path": "README.md"},
                        "risk_level": "safe",
                        "requires_approval": True,
                        "options": ["allow", "deny"],
                    }
                ],
                "usage": {"input_tokens": 60, "output_tokens": 12},
            }
        ],
    }


def _multi_turn_script() -> dict[str, Any]:
    return {
        "version": 1,
        "on_exhausted": "error",
        "turns": [
            {"text": "first", "usage": {"output_tokens": 1}},
            {"text": "second", "usage": {"output_tokens": 2}},
            {"text": "third", "usage": {"output_tokens": 3}},
        ],
    }


@pytest.fixture
def enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Bind the opt-in to an isolated home; return a script-installing callable."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(HOME_ENV_VAR, str(home))

    def install(script: dict[str, Any], name: str = "script.json") -> Path:
        path = _write_script(tmp_path, script, name)
        monkeypatch.setenv(SCRIPT_ENV_VAR, str(path))
        return path

    return install


async def _collect(agen) -> list[Any]:
    return [event async for event in agen]


def _as_tuples(events: list[Any]) -> list[tuple]:
    """Comparable, hashable snapshot of a full event stream (determinism oracle)."""
    return [tuple(sorted(dataclasses.asdict(e).items(), key=str)) for e in events]


# ── The safety gate ───────────────────────────────────────────────────


def test_refuses_to_construct_without_the_optin_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No opt-in => loud typed refusal. This is what stops an accidental binding."""
    monkeypatch.delenv(SCRIPT_ENV_VAR, raising=False)
    monkeypatch.setenv(HOME_ENV_VAR, str(tmp_path))
    with pytest.raises(ScriptedProviderNotEnabled) as excinfo:
        ScriptedProvider()
    assert SCRIPT_ENV_VAR in str(excinfo.value)


def test_refusal_is_not_a_silent_no_op_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal must escape as an exception, never a constructed dud instance."""
    monkeypatch.delenv(SCRIPT_ENV_VAR, raising=False)
    monkeypatch.setenv(HOME_ENV_VAR, str(tmp_path))
    provider: ScriptedProvider | None = None
    try:
        provider = ScriptedProvider()
    except ScriptedProviderNotEnabled:
        pass
    assert provider is None


def test_refuses_when_home_is_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An opted-in script with the DEFAULT home would answer in the user's real home."""
    monkeypatch.setenv(SCRIPT_ENV_VAR, str(_write_script(tmp_path, _text_script())))
    monkeypatch.delenv(HOME_ENV_VAR, raising=False)
    with pytest.raises(ScriptedProviderRefused):
        ScriptedProvider()


def test_refuses_when_home_is_explicitly_the_real_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pointing the home AT the real home is refused too, and without mkdir'ing it."""
    import personalclaw.config.loader as loader

    def _poisoned() -> Path:  # pragma: no cover - must never be called
        raise AssertionError("the gate must not call config_dir(): it mkdir's the home")

    monkeypatch.setattr(loader, "config_dir", _poisoned)
    monkeypatch.setenv(SCRIPT_ENV_VAR, str(_write_script(tmp_path, _text_script())))
    monkeypatch.setenv(HOME_ENV_VAR, str(Path.home() / loader.CONFIG_DIR_NAME))
    with pytest.raises(ScriptedProviderRefused):
        ScriptedProvider()


def test_gate_never_creates_the_real_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """The no-opt-in path must not touch the filesystem at all."""
    import personalclaw.config.loader as loader

    def _poisoned() -> Path:  # pragma: no cover - must never be called
        raise AssertionError("the gate must not call config_dir(): it mkdir's the home")

    monkeypatch.setattr(loader, "config_dir", _poisoned)
    monkeypatch.delenv(SCRIPT_ENV_VAR, raising=False)
    with pytest.raises(ScriptedProviderNotEnabled):
        resolve_script_path()


def test_refuses_when_the_script_file_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SCRIPT_ENV_VAR, str(tmp_path / "nope.json"))
    monkeypatch.setenv(HOME_ENV_VAR, str(tmp_path))
    with pytest.raises(ScriptedScriptError):
        ScriptedProvider()


def test_no_constructor_argument_can_bypass_the_gate() -> None:
    """A ``script_path`` kwarg would be a hole in the gate; there must not be one."""
    import inspect

    params = list(inspect.signature(ScriptedProvider.__init__).parameters)
    assert params == ["self"]


@pytest.mark.asyncio
async def test_start_re_asserts_the_gate(enabled, monkeypatch: pytest.MonkeyPatch) -> None:
    """PERSONALCLAW_HOME is read live everywhere; a home that moves must still refuse."""
    enabled(_text_script())
    provider = ScriptedProvider()
    await provider.start()  # passes while the isolated home is bound
    monkeypatch.delenv(HOME_ENV_VAR, raising=False)
    with pytest.raises(ScriptedProviderRefused):
        await provider.start()


# ── A bound instance is usable ────────────────────────────────────────


@pytest.mark.asyncio
async def test_bound_instance_completes_a_scripted_text_turn(enabled) -> None:
    """The done-when clause: a real bound provider, a real turn, zero credentials."""
    enabled(_text_script())
    provider = ScriptedProvider()
    await provider.start()

    events = await _collect(provider.stream("hello"))

    assert [e.kind for e in events] == [EVENT_TEXT_CHUNK, EVENT_TEXT_CHUNK, EVENT_COMPLETE]
    assert "".join(e.text for e in events[:2]) == "Hello from the scripted provider."
    done = events[-1]
    assert (done.input_tokens, done.output_tokens) == (42, 7)
    assert (done.cache_creation_tokens, done.cache_read_tokens) == (3, 11)
    assert done.stop_reason == "end_turn"
    assert done.duration_ms == 250
    assert done.context_usage_pct == 12.5
    assert provider.context_usage_pct() == 12.5
    await provider.shutdown()


def test_provider_declares_tool_support(enabled) -> None:
    enabled(_tool_script())
    assert ScriptedProvider().supports_tools is True


@pytest.mark.asyncio
async def test_tool_call_turn_emits_permission_then_call(enabled) -> None:
    enabled(_tool_script())
    provider = ScriptedProvider()

    events = await _collect(provider.stream("read it"))

    assert [e.kind for e in events] == [
        EVENT_THINKING_CHUNK,
        EVENT_TEXT_CHUNK,
        EVENT_PERMISSION_REQUEST,
        EVENT_TOOL_CALL,
        EVENT_COMPLETE,
    ]
    request = events[2]
    assert request.request_id == "call-1"
    assert request.options == ["allow", "deny"]
    call = events[3]
    assert (call.tool_call_id, call.title, call.risk_level) == ("call-1", "read_file", "safe")
    assert call.tool_input == {"path": "README.md"}
    assert provider.pending_tool_calls == {"call-1": "read_file"}


@pytest.mark.asyncio
async def test_approve_tool_resolves_the_pending_request(enabled) -> None:
    enabled(_tool_script())
    provider = ScriptedProvider()
    await _collect(provider.stream("read it"))

    await provider.approve_tool("call-1")

    assert provider.pending_tool_calls == {}
    assert provider.decisions == [("call-1", "approved")]


@pytest.mark.asyncio
async def test_reject_tool_resolves_the_pending_request(enabled) -> None:
    enabled(_tool_script())
    provider = ScriptedProvider()
    await _collect(provider.stream("read it"))

    await provider.reject_tool("call-1")

    assert provider.pending_tool_calls == {}
    assert provider.decisions == [("call-1", "rejected")]


@pytest.mark.asyncio
async def test_resolving_an_unknown_id_is_recorded_not_raised(enabled) -> None:
    """A fixture must not turn a caller's defensive approve into a mid-turn crash."""
    enabled(_text_script())
    provider = ScriptedProvider()

    await provider.approve_tool("never-requested")

    assert provider.decisions == [("never-requested", "approved")]


@pytest.mark.asyncio
async def test_nth_prompt_gets_the_nth_scripted_response(enabled) -> None:
    enabled(_multi_turn_script())
    provider = ScriptedProvider()

    replies = []
    for prompt in ("one", "two", "three"):
        events = await _collect(provider.stream(prompt))
        replies.append("".join(e.text for e in events if e.kind == EVENT_TEXT_CHUNK))

    assert replies == ["first", "second", "third"]
    assert provider.turn_index == 3


@pytest.mark.asyncio
async def test_exhausted_error_mode_raises(enabled) -> None:
    enabled(_multi_turn_script())
    provider = ScriptedProvider()
    for prompt in ("one", "two", "three"):
        await _collect(provider.stream(prompt))
    with pytest.raises(ScriptedScriptError):
        await _collect(provider.stream("four"))


@pytest.mark.asyncio
async def test_exhausted_repeat_last_is_the_default(enabled) -> None:
    enabled({"version": 1, "turns": [{"text": "only"}]})
    provider = ScriptedProvider()

    first = await _collect(provider.stream("a"))
    second = await _collect(provider.stream("b"))

    assert _as_tuples(first) == _as_tuples(second)


@pytest.mark.asyncio
async def test_expect_prompt_guards_the_turn(enabled) -> None:
    enabled({"version": 1, "turns": [{"expect_prompt": "weather", "text": "sunny"}]})
    provider = ScriptedProvider()
    with pytest.raises(ScriptedScriptError):
        await _collect(provider.stream("what time is it"))


@pytest.mark.asyncio
async def test_context_usage_pct_is_none_when_the_script_omits_it(enabled) -> None:
    """``None`` means 'not measured' and is a different answer from ``0.0``."""
    enabled({"version": 1, "turns": [{"text": "hi"}]})
    provider = ScriptedProvider()
    assert provider.context_usage_pct() is None

    events = await _collect(provider.stream("hi"))

    assert events[-1].context_usage_pct is None
    assert provider.context_usage_pct() is None


@pytest.mark.asyncio
async def test_per_turn_context_usage_pct_overrides_the_script_default(enabled) -> None:
    enabled(
        {
            "version": 1,
            "context_usage_pct": 5.0,
            "turns": [{"text": "a"}, {"text": "b", "context_usage_pct": 44.0}],
        }
    )
    provider = ScriptedProvider()

    await _collect(provider.stream("a"))
    assert provider.context_usage_pct() == 5.0
    await _collect(provider.stream("b"))
    assert provider.context_usage_pct() == 44.0


@pytest.mark.asyncio
async def test_complete_walks_the_same_script(enabled) -> None:
    """The reasoning-axis path must be scripted too, and share the turn counter."""
    enabled(_multi_turn_script())
    provider = ScriptedProvider()

    first = await _collect(provider.complete([{"role": "user", "content": "one"}]))
    second = await _collect(provider.stream("two"))

    assert "".join(e.text for e in first if e.kind == EVENT_TEXT_CHUNK) == "first"
    assert "".join(e.text for e in second if e.kind == EVENT_TEXT_CHUNK) == "second"
    assert provider.turn_index == 2


@pytest.mark.asyncio
async def test_complete_records_its_kwargs_without_reacting_to_them(enabled) -> None:
    """Reacting to tools/model/effort would break the byte-identical guarantee."""
    enabled({"version": 1, "turns": [{"text": "same"}]})
    a = ScriptedProvider()
    b = ScriptedProvider()

    events_a = await _collect(a.complete([{"role": "user", "content": "q"}]))
    events_b = await _collect(
        b.complete(
            [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}],
            tools=[{"name": "t"}],
            model="gpt-nonexistent",
            reasoning_effort="max",
        )
    )

    assert _as_tuples(events_a) == _as_tuples(events_b)
    assert b.last_complete_call == {
        "message_count": 2,
        "tool_count": 1,
        "model": "gpt-nonexistent",
        "reasoning_effort": "max",
    }


# ── Determinism ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_script_and_prompts_produce_byte_identical_events(
    enabled, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two instances, different wall clocks and RNG states, identical event streams."""
    import time

    enabled(_multi_turn_script())
    prompts = ("one", "two", "three")

    monkeypatch.setattr(time, "time", lambda: 1_000_000.0)
    monkeypatch.setattr(time, "monotonic", lambda: 1.0)
    random.seed(1)
    first = ScriptedProvider()
    run_one = [ev for p in prompts for ev in await _collect(first.stream(p))]

    monkeypatch.setattr(time, "time", lambda: 2_000_000.0)
    monkeypatch.setattr(time, "monotonic", lambda: 99_999.0)
    random.seed(2)
    second = ScriptedProvider()
    run_two = [ev for p in prompts for ev in await _collect(second.stream(p))]

    assert _as_tuples(run_one) == _as_tuples(run_two)
    # Belt and braces: the serialized stream is literally byte-identical.
    assert json.dumps([dataclasses.asdict(e) for e in run_one], sort_keys=True) == json.dumps(
        [dataclasses.asdict(e) for e in run_two], sort_keys=True
    )


@pytest.mark.asyncio
async def test_token_counts_come_from_the_script(enabled) -> None:
    """Telemetry has something to render, and it is the script's number, not a guess."""
    enabled(_multi_turn_script())
    provider = ScriptedProvider()

    outputs = []
    for prompt in ("one", "two", "three"):
        events = await _collect(provider.stream(prompt))
        outputs.append(events[-1].output_tokens)

    assert outputs == [1, 2, 3]


# ── Zero network, proven ──────────────────────────────────────────────


def _module_file(module: str) -> Path | None:
    direct = SRC_ROOT / (module.replace(".", "/") + ".py")
    if direct.is_file():
        return direct
    package = SRC_ROOT / module.replace(".", "/") / "__init__.py"
    return package if package.is_file() else None


def _scan_body(body: list[ast.stmt]) -> list[ast.stmt]:
    """Import statements reachable at module scope, descending into if/try wrappers."""
    found: list[ast.stmt] = []
    for statement in body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            found.append(statement)
        elif isinstance(statement, (ast.If, ast.Try)):
            found.extend(_scan_body(statement.body))
            found.extend(_scan_body(statement.orelse))
            for handler in getattr(statement, "handlers", []):
                found.extend(_scan_body(handler.body))
            found.extend(_scan_body(getattr(statement, "finalbody", [])))
    return found


def _imported_names(statements: list[ast.stmt], module: str) -> list[str]:
    names: list[str] = []
    for statement in statements:
        if isinstance(statement, ast.Import):
            names.extend(alias.name for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom):
            base = statement.module or ""
            if statement.level:
                parts = module.split(".")
                head = parts[: -statement.level]
                base = ".".join(head + ([base] if base else []))
            names.append(base)
    return names


def test_module_scope_import_closure_declares_no_http_client() -> None:
    """AST proof, not a grep: nothing in the closure DECLARES a network dependency.

    The root module is scanned exhaustively (every import, including function-level
    ones — a lazy ``import httpx`` inside this module is still this module's business).
    First-party modules it pulls are scanned at module scope, which is what actually
    executes on import.
    """
    root = "personalclaw.llm.scripted"
    root_file = _module_file(root)
    assert root_file is not None, "scripted.py not found under src/"

    root_tree = ast.parse(root_file.read_text(encoding="utf-8"))
    root_statements = [
        node for node in ast.walk(root_tree) if isinstance(node, (ast.Import, ast.ImportFrom))
    ]

    closure: set[str] = set()
    offenders: dict[str, list[str]] = {}
    queue: list[tuple[str, list[ast.stmt]]] = [(root, root_statements)]
    while queue:
        module, statements = queue.pop()
        if module in closure:
            continue
        closure.add(module)
        for name in _imported_names(statements, module):
            top = name.split(".")[0]
            if top == "personalclaw":
                child = _module_file(name)
                if child is not None and name not in closure:
                    tree = ast.parse(child.read_text(encoding="utf-8"))
                    queue.append((name, _scan_body(tree.body)))
            elif top in FORBIDDEN_IMPORTS:
                offenders.setdefault(module, []).append(name)

    assert offenders == {}, f"network-capable imports declared in the closure: {offenders}"
    # Vacuity guard: an empty closure or a walk that found nothing would also pass.
    assert root in closure and "personalclaw.llm.base" in closure
    assert len(root_statements) >= 5


def test_importing_the_module_loads_no_networking_module() -> None:
    """Runtime proof: the DELTA this module adds to sys.modules is network-free.

    Scoped to the delta on purpose. ``personalclaw/__init__.py`` and
    ``personalclaw/llm/__init__.py`` already load ``socket``/``ssl``/``urllib``/
    ``jsonschema`` before this module's first line runs, so asserting on the absolute
    set would measure them, not this module. The delta measures exactly this module.
    """
    probe = """
import json, sys
import personalclaw.llm            # package __init__ baseline
before = set(sys.modules)
import personalclaw.llm.scripted   # noqa: F401
delta = sorted(set(sys.modules) - before)
print(json.dumps(delta))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(SRC_ROOT), "PATH": "/usr/bin:/bin"},
        check=True,
    )
    delta = json.loads(result.stdout.strip().splitlines()[-1])
    # Vacuity guard: the probe must actually have imported something new.
    assert "personalclaw.llm.scripted" in delta, f"probe imported nothing new: {delta}"
    leaked = sorted({name.split(".")[0] for name in delta} & FORBIDDEN_IMPORTS)
    assert leaked == [], f"importing scripted.py pulled networking modules: {leaked}"


# ── Strict script validation (a typo in a fixture must be loud) ────────


@pytest.mark.parametrize(
    "script",
    [
        pytest.param({"turns": [{"text": "x"}]}, id="missing-version"),
        pytest.param({"version": 2, "turns": [{"text": "x"}]}, id="wrong-version"),
        pytest.param({"version": 1, "turns": []}, id="empty-turns"),
        pytest.param({"version": 1}, id="no-turns"),
        pytest.param({"version": 1, "turns": [{"text": "x"}], "extra": 1}, id="unknown-top-key"),
        pytest.param({"version": 1, "turns": [{"chunk": ["x"]}]}, id="unknown-turn-key"),
        pytest.param(
            {"version": 1, "turns": [{"text": "a", "chunks": ["b"]}]}, id="text-and-chunks"
        ),
        pytest.param({"version": 1, "turns": [{"chunks": "not-a-list"}]}, id="chunks-not-a-list"),
        pytest.param({"version": 1, "turns": [{"usage": {"nope": 1}}]}, id="unknown-usage-key"),
        pytest.param(
            {"version": 1, "turns": [{"usage": {"input_tokens": "x"}}]}, id="usage-not-int"
        ),
        pytest.param(
            {"version": 1, "turns": [{"tool_calls": [{"name": "t"}]}]}, id="tool-call-no-id"
        ),
        pytest.param(
            {"version": 1, "turns": [{"tool_calls": [{"id": "a"}]}]}, id="tool-call-no-name"
        ),
        pytest.param(
            {"version": 1, "turns": [{"tool_calls": [{"id": "a", "name": "t", "options": ["x"]}]}]},
            id="options-without-approval",
        ),
        pytest.param(
            {"version": 1, "on_exhausted": "explode", "turns": [{}]}, id="bad-on-exhausted"
        ),
        pytest.param(
            {"version": 1, "context_usage_pct": "high", "turns": [{}]}, id="pct-not-a-number"
        ),
        pytest.param({"version": 1, "turns": "nope"}, id="turns-not-a-list"),
    ],
)
def test_malformed_script_raises_loudly(
    script: dict[str, Any], enabled, monkeypatch: pytest.MonkeyPatch
) -> None:
    enabled(script)
    with pytest.raises(ScriptedScriptError):
        ScriptedProvider()


def test_non_json_script_raises_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv(SCRIPT_ENV_VAR, str(path))
    monkeypatch.setenv(HOME_ENV_VAR, str(tmp_path / "home"))
    with pytest.raises(ScriptedScriptError):
        ScriptedProvider()
