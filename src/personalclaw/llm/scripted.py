"""Deterministic scripted model provider — a real bound provider with zero network.

``ScriptedProvider`` is a :class:`~personalclaw.llm.base.ModelProvider` whose whole
turn stream comes out of a JSON file. It exists so a gateway can boot and complete a
real chat turn with **no credentials and no network** — the offline half of the e2e
floor. It is a fixture, not a model: it never infers anything, it replays.

Why a file and not a Python fixture object: the e2e boot, a pytest test and a
hand-run ``personalclaw gateway`` all need to drive *different* scripts, and only one
of those three can pass a Python object. A path keeps the script a piece of test data
rather than a code change.

Design constraint that shapes everything below: **byte-identical output for the same
script and the same prompt sequence.** Nothing emitted is derived from the wall clock,
``random``, the prompt text, the tool list, the model name or the reasoning effort.
Token counts, ``duration_ms`` and context usage all come out of the script. The only
state that advances is a turn counter, so turn *N* of a process always emits turn *N*
of the script.

Zero network by construction: this module imports ``json``/``os``/``pathlib`` plus
``llm.base`` and ``llm.events``. ``tests/test_scripted_provider.py`` proves it two ways
rather than grepping for a word — it walks the ASTs of this module and its module-scope
first-party closure for any HTTP client, socket or vendor SDK, and it measures in a
subprocess that importing this module adds **no** networking module to ``sys.modules``
beyond what ``import personalclaw.llm`` already loaded (the package ``__init__``s pull
``socket``/``ssl``/``urllib``/``jsonschema`` on their own; this module adds nothing).

Safety gate — why it takes the strong option
--------------------------------------------
A fake provider that could be bound in a real home would fabricate model answers under
the user's own assistant identity, and nothing downstream can tell a replayed sentence
from an inferred one. So construction is refused unless **two independent conditions**
hold, and every refusal is a loud typed error (never a silent degrade to a no-op
provider, which would look like a working assistant giving canned answers):

1. ``PERSONALCLAW_SCRIPTED_MODEL_SCRIPT`` names a readable script file. The env var
   *is* the script path rather than a boolean, so enabling the fake and saying exactly
   what it will reply are the same act — there is no "on" state with a built-in default
   reply. A stray ``=1`` in a shell profile cannot enable it. ``PERSONALCLAW_``-prefixed
   per repo convention; ``MODEL`` in the name keeps it distinct from harness variables.
2. ``PERSONALCLAW_HOME`` is set and does not resolve to ``~/.personalclaw``.

Condition 2 is the deliberate choice of the stronger option, for a measured reason:
``save_credential()`` (``config/loader.py``) mirrors *any* stored credential key into
``os.environ``, so an env-only gate is in principle reachable from the credential store
— a surface reachable through the API. The home condition is not reachable that way in
any harmful form: smuggling ``PERSONALCLAW_HOME`` through the same mirror relocates the
home *away* from the real one, which is precisely the safe outcome. The property this
buys is the one worth having — **the fake can never answer inside the user's real
assistant home** — and the cost is one env var in an e2e boot and in tests, both of
which already run against an isolated home. This follows the in-repo precedent that
``--approval yolo`` is refused unless ``PERSONALCLAW_HOME`` is explicitly non-default
and ``--seed`` refuses the real home outright.

Neither condition is reachable by ``config.json`` alone: config carries no environment
block, and the provider reads the environment, never config. Both are re-checked in
:meth:`ScriptedProvider.start` as well as ``__init__``, because ``PERSONALCLAW_HOME`` is
re-read live throughout the codebase and a home that moved between construction and
start would otherwise slip through.

Script file format (version 1)
------------------------------
::

    {
      "version": 1,                        // required, must be 1
      "context_usage_pct": 12.5,           // optional; OMITTED => None
      "on_exhausted": "repeat_last",       // "repeat_last" (default) | "error"
      "turns": [                           // required, non-empty; Nth prompt -> Nth turn
        {
          "expect_prompt": "hello",        // optional substring guard on the prompt
          "text": "Hi there.",             // one text chunk  (mutually exclusive
          "chunks": ["Hi ", "there."],     //   with a list of chunks)
          "thinking": ["weighing it"],     // optional thinking chunks, emitted first
          "tool_calls": [
            {
              "id": "call-1",              // required
              "name": "read_file",         // required (becomes LLMEvent.title)
              "input": {"path": "R.md"},   // optional, any JSON
              "risk_level": "safe",        // optional: safe|caution|destructive
              "requires_approval": true,   // optional: emit a PERMISSION_REQUEST first
              "options": ["allow", "deny"] // optional, only with requires_approval
            }
          ],
          "stop_reason": "end_turn",       // optional
          "usage": {                       // optional; each field defaults to 0
            "input_tokens": 42, "output_tokens": 9,
            "cache_creation_tokens": 0, "cache_read_tokens": 0
          },
          "context_usage_pct": 13.0,       // optional per-turn override
          "duration_ms": 0                 // optional
        }
      ]
    }

Unknown keys are rejected at every level. A fixture is only useful if a typo in it is
loud, so ``"chunk"`` for ``"chunks"`` raises instead of silently emitting nothing.

Event order within a turn mirrors what the real adapters emit: thinking chunks, then
text chunks, then for each tool call an optional ``EVENT_PERMISSION_REQUEST`` followed
by ``EVENT_TOOL_CALL``, then exactly one terminal ``EVENT_COMPLETE`` carrying the
token counts and ``context_usage_pct``.

``context_usage_pct`` is ``None`` when the script omits it. ``None`` and ``0.0`` are
different answers in this codebase — ``None`` means "never measured" and consumers must
omit the number rather than render a fabricated 0% — so a script that says nothing about
context usage produces ``None``, not zero.

Approvals: ``requires_approval`` tool calls are recorded as pending and resolved by
:meth:`ScriptedProvider.approve_tool` / :meth:`ScriptedProvider.reject_tool`, whose
decisions are readable via :attr:`ScriptedProvider.decisions`. Resolving an id that was
never pending is recorded, not raised — a fixture must not turn a caller's defensive
approve into a crash mid-turn. What tests assert is the recorded decision list.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from personalclaw.llm.base import ModelProvider
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    EVENT_TOOL_CALL,
)
from personalclaw.llm.events import AgentEvent as LLMEvent

#: The opt-in. Names the script path, so "enabled" and "what it will say" are one act.
SCRIPT_ENV_VAR = "PERSONALCLAW_SCRIPTED_MODEL_SCRIPT"

#: The home override the second lock reads. Set + non-real is the only accepted state.
HOME_ENV_VAR = "PERSONALCLAW_HOME"

SCRIPT_VERSION = 1

_TOP_LEVEL_KEYS = frozenset({"version", "context_usage_pct", "on_exhausted", "turns"})
_TURN_KEYS = frozenset(
    {
        "expect_prompt",
        "text",
        "chunks",
        "thinking",
        "tool_calls",
        "stop_reason",
        "usage",
        "context_usage_pct",
        "duration_ms",
    }
)
_TOOL_CALL_KEYS = frozenset({"id", "name", "input", "risk_level", "requires_approval", "options"})
_USAGE_KEYS = frozenset(
    {"input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens"}
)
_ON_EXHAUSTED = frozenset({"repeat_last", "error"})


class ScriptedProviderError(RuntimeError):
    """Base for every scripted-provider refusal. Always raised, never swallowed."""


class ScriptedProviderNotEnabled(ScriptedProviderError):
    """The opt-in env var naming a script is absent — the fake is not enabled."""


class ScriptedProviderRefused(ScriptedProviderError):
    """The opt-in is present but the active home is (or defaults to) the real home."""


class ScriptedScriptError(ScriptedProviderError):
    """The script file is missing, unreadable, or malformed."""


def _real_home() -> Path:
    """The user's real assistant home — the one place the fake must never answer in.

    ``CONFIG_DIR_NAME`` is imported lazily on purpose: taking it at module scope would
    add ``config.loader``'s whole closure to this module's declared import graph for a
    single string constant that is only needed at gate time, when the config system is
    loaded anyway. Importing it rather than re-spelling ``".personalclaw"`` keeps one
    source of truth for the home's name.
    """
    from personalclaw.config.loader import CONFIG_DIR_NAME

    return (Path.home() / CONFIG_DIR_NAME).resolve()


def resolve_script_path() -> Path:
    """Return the opted-in script path, or raise a typed refusal.

    Deliberately does **not** call ``config_dir()``: that helper ``mkdir``s the home it
    resolves, so using it to detect "this is the real home" would create
    ``~/.personalclaw`` as a side effect of refusing to touch it.
    """
    raw = os.environ.get(SCRIPT_ENV_VAR, "").strip()
    if not raw:
        raise ScriptedProviderNotEnabled(
            f"ScriptedProvider is a test fixture and is disabled by default. Set "
            f"{SCRIPT_ENV_VAR} to a script file path to enable it; there is no "
            f"default script and no config setting that can enable it."
        )

    override = os.environ.get(HOME_ENV_VAR, "").strip()
    if not override:
        raise ScriptedProviderRefused(
            f"ScriptedProvider refuses to run against the default home "
            f"({_real_home()}): it would fabricate model answers under the user's real "
            f"assistant identity. Set {HOME_ENV_VAR} to an isolated directory."
        )
    active_home = Path(override).expanduser().resolve()
    if active_home == _real_home():
        raise ScriptedProviderRefused(
            f"ScriptedProvider refuses to run against the real home ({active_home}): "
            f"it would fabricate model answers under the user's real assistant "
            f"identity. Point {HOME_ENV_VAR} at an isolated directory."
        )

    path = Path(raw).expanduser()
    if not path.is_file():
        raise ScriptedScriptError(f"{SCRIPT_ENV_VAR}={raw!r} does not name a readable file.")
    return path


def _require_mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScriptedScriptError(f"{where} must be a JSON object, got {type(value).__name__}")
    return value


def _reject_unknown(obj: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise ScriptedScriptError(
            f"{where} has unknown key(s) {unknown}; allowed: {sorted(allowed)}"
        )


def _require_str(obj: dict[str, Any], key: str, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value:
        raise ScriptedScriptError(f"{where} requires a non-empty string {key!r}")
    return value


def _optional_pct(obj: dict[str, Any], where: str) -> float | None:
    if "context_usage_pct" not in obj:
        return None
    value = obj["context_usage_pct"]
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ScriptedScriptError(f"{where}.context_usage_pct must be a number or null")
    return float(value)


def _parse_usage(raw: Any, where: str) -> dict[str, int]:
    usage = _require_mapping(raw, where) if raw is not None else {}
    _reject_unknown(usage, _USAGE_KEYS, where)
    out: dict[str, int] = {}
    for key in sorted(_USAGE_KEYS):
        value = usage.get(key, 0)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ScriptedScriptError(f"{where}.{key} must be an integer")
        out[key] = value
    return out


def _parse_chunks(turn: dict[str, Any], where: str) -> list[str]:
    if "text" in turn and "chunks" in turn:
        raise ScriptedScriptError(f"{where} sets both 'text' and 'chunks'; pick one")
    if "text" in turn:
        text = turn["text"]
        if not isinstance(text, str):
            raise ScriptedScriptError(f"{where}.text must be a string")
        return [text] if text else []
    chunks = turn.get("chunks", [])
    if not isinstance(chunks, list) or any(not isinstance(c, str) for c in chunks):
        raise ScriptedScriptError(f"{where}.chunks must be a list of strings")
    return list(chunks)


def _parse_thinking(turn: dict[str, Any], where: str) -> list[str]:
    thinking = turn.get("thinking", [])
    if not isinstance(thinking, list) or any(not isinstance(t, str) for t in thinking):
        raise ScriptedScriptError(f"{where}.thinking must be a list of strings")
    return list(thinking)


def _parse_tool_calls(turn: dict[str, Any], where: str) -> list[dict[str, Any]]:
    raw_calls = turn.get("tool_calls", [])
    if not isinstance(raw_calls, list):
        raise ScriptedScriptError(f"{where}.tool_calls must be a list")
    calls: list[dict[str, Any]] = []
    for i, raw_call in enumerate(raw_calls):
        spot = f"{where}.tool_calls[{i}]"
        call = _require_mapping(raw_call, spot)
        _reject_unknown(call, _TOOL_CALL_KEYS, spot)
        requires_approval = call.get("requires_approval", False)
        if not isinstance(requires_approval, bool):
            raise ScriptedScriptError(f"{spot}.requires_approval must be a boolean")
        options = call.get("options", [])
        if not isinstance(options, list):
            raise ScriptedScriptError(f"{spot}.options must be a list")
        if options and not requires_approval:
            raise ScriptedScriptError(
                f"{spot} sets 'options' without 'requires_approval'; options are only "
                f"emitted on a permission request"
            )
        risk_level = call.get("risk_level", "")
        if not isinstance(risk_level, str):
            raise ScriptedScriptError(f"{spot}.risk_level must be a string")
        calls.append(
            {
                "id": _require_str(call, "id", spot),
                "name": _require_str(call, "name", spot),
                "input": call.get("input", ""),
                "risk_level": risk_level,
                "requires_approval": requires_approval,
                "options": list(options),
            }
        )
    return calls


def _parse_turn(raw_turn: Any, index: int, script_pct: float | None) -> dict[str, Any]:
    where = f"turns[{index}]"
    turn = _require_mapping(raw_turn, where)
    _reject_unknown(turn, _TURN_KEYS, where)
    expect_prompt = turn.get("expect_prompt", "")
    if not isinstance(expect_prompt, str):
        raise ScriptedScriptError(f"{where}.expect_prompt must be a string")
    stop_reason = turn.get("stop_reason", "")
    if not isinstance(stop_reason, str):
        raise ScriptedScriptError(f"{where}.stop_reason must be a string")
    duration_ms = turn.get("duration_ms", 0)
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool):
        raise ScriptedScriptError(f"{where}.duration_ms must be an integer")
    return {
        "expect_prompt": expect_prompt,
        "thinking": _parse_thinking(turn, where),
        "chunks": _parse_chunks(turn, where),
        "tool_calls": _parse_tool_calls(turn, where),
        "stop_reason": stop_reason,
        "usage": _parse_usage(turn.get("usage"), f"{where}.usage"),
        "context_usage_pct": (
            _optional_pct(turn, where) if "context_usage_pct" in turn else script_pct
        ),
        "duration_ms": duration_ms,
    }


def load_script(path: Path) -> dict[str, Any]:
    """Parse + validate a script file into the internal turn list.

    Validation is strict (unknown keys rejected, types checked) because the only value
    a fixture has is that a mistake in it is loud rather than a silently empty turn.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScriptedScriptError(f"cannot read script {path}: {exc}") from exc
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ScriptedScriptError(f"script {path} is not valid JSON: {exc}") from exc

    script = _require_mapping(data, f"script {path}")
    _reject_unknown(script, _TOP_LEVEL_KEYS, f"script {path}")
    if script.get("version") != SCRIPT_VERSION:
        raise ScriptedScriptError(
            f"script {path} has version {script.get('version')!r}; "
            f"this build understands version {SCRIPT_VERSION}"
        )
    on_exhausted = script.get("on_exhausted", "repeat_last")
    if on_exhausted not in _ON_EXHAUSTED:
        raise ScriptedScriptError(
            f"script {path}.on_exhausted must be one of {sorted(_ON_EXHAUSTED)}"
        )
    raw_turns = script.get("turns")
    if not isinstance(raw_turns, list) or not raw_turns:
        raise ScriptedScriptError(f"script {path} requires a non-empty 'turns' list")

    script_pct = _optional_pct(script, f"script {path}")
    turns = [_parse_turn(t, i, script_pct) for i, t in enumerate(raw_turns)]
    return {"on_exhausted": on_exhausted, "turns": turns}


class ScriptedProvider(ModelProvider):
    """A ModelProvider that replays a JSON script. Deterministic, offline, gated.

    Construction reads the gate (see :func:`resolve_script_path`) and raises a typed
    :class:`ScriptedProviderError` when it does not pass. There is intentionally **no**
    ``script_path`` constructor argument: a code path that accepts a script directly
    would be a bypass of the gate, so the environment is the single source and tests
    monkeypatch it like any other caller.
    """

    # The script can emit tool calls, so the loop must be willing to hand it tools.
    supports_tools: bool = True

    def __init__(self) -> None:
        self._script_path = resolve_script_path()
        self._script = load_script(self._script_path)
        self._turn_index = 0
        self._pending: dict[str, str] = {}
        self._decisions: list[tuple[str, str]] = []
        self._last_context_pct: float | None = None
        self._last_complete_call: dict[str, Any] = {}

    # ── Introspection (what tests and a harness assert against) ───────

    @property
    def script_path(self) -> Path:
        return self._script_path

    @property
    def turn_index(self) -> int:
        """How many turns have been served. The only state that advances."""
        return self._turn_index

    @property
    def decisions(self) -> list[tuple[str, str]]:
        """``(tool_call_id, "approved"|"rejected")`` in the order they were resolved."""
        return list(self._decisions)

    @property
    def pending_tool_calls(self) -> dict[str, str]:
        """Unresolved permission requests, ``{tool_call_id: tool_name}``."""
        return dict(self._pending)

    @property
    def last_complete_call(self) -> dict[str, Any]:
        """The kwargs of the last :meth:`complete` call.

        Recorded, never reacted to: letting ``tools``/``model``/``reasoning_effort``
        change the emitted events would break the byte-identical guarantee.
        """
        return dict(self._last_complete_call)

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Re-assert the gate. ``PERSONALCLAW_HOME`` is read live across the codebase,
        so a home that moved between construction and start must still be refused."""
        resolve_script_path()

    async def shutdown(self) -> None:
        """Nothing to tear down — no process, no connection, no session files."""
        return None

    # ── Turn emission ─────────────────────────────────────────────────

    def _next_turn(self, prompt: str) -> dict[str, Any]:
        turns: list[dict[str, Any]] = self._script["turns"]
        index = self._turn_index
        if index >= len(turns):
            if self._script["on_exhausted"] == "error":
                raise ScriptedScriptError(
                    f"script {self._script_path} has {len(turns)} turn(s) but prompt "
                    f"{index + 1} was sent (on_exhausted='error')"
                )
            index = len(turns) - 1
        turn = turns[index]
        expect = turn["expect_prompt"]
        if expect and expect not in prompt:
            raise ScriptedScriptError(
                f"script {self._script_path} turns[{index}] expects a prompt containing "
                f"{expect!r} but got {prompt!r}"
            )
        self._turn_index += 1
        return turn

    async def _emit(self, prompt: str) -> AsyncIterator[LLMEvent]:
        turn = self._next_turn(prompt)
        for thought in turn["thinking"]:
            yield LLMEvent(kind=EVENT_THINKING_CHUNK, text=thought)
        for chunk in turn["chunks"]:
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=chunk)
        for call in turn["tool_calls"]:
            if call["requires_approval"]:
                self._pending[call["id"]] = call["name"]
                yield LLMEvent(
                    kind=EVENT_PERMISSION_REQUEST,
                    tool_call_id=call["id"],
                    title=call["name"],
                    risk_level=call["risk_level"],
                    request_id=call["id"],
                    options=list(call["options"]),
                    tool_input=call["input"],
                )
            yield LLMEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=call["id"],
                title=call["name"],
                risk_level=call["risk_level"],
                tool_input=call["input"],
            )
        usage: dict[str, int] = turn["usage"]
        self._last_context_pct = turn["context_usage_pct"]
        yield LLMEvent(
            kind=EVENT_COMPLETE,
            stop_reason=turn["stop_reason"],
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_creation_tokens=usage["cache_creation_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            context_usage_pct=self._last_context_pct,
            duration_ms=turn["duration_ms"],
        )

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        async for event in self._emit(message):
            yield event

    async def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        reasoning_effort: str = "",
    ) -> AsyncIterator[LLMEvent]:
        """Stateless multi-message entry point (the reasoning-axis / native-loop path).

        Advances the same turn counter as :meth:`stream`, so a caller mixing the two
        still walks the script in order.
        """
        self._last_complete_call = {
            "message_count": len(messages),
            "tool_count": len(tools or []),
            "model": model or "",
            "reasoning_effort": reasoning_effort,
        }
        last_user = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                last_user = str(message.get("content", ""))
                break
        async for event in self._emit(last_user):
            yield event

    # ── Tool approval ─────────────────────────────────────────────────

    async def approve_tool(self, request_id: str | int) -> None:
        self._resolve(request_id, "approved")

    async def reject_tool(self, request_id: str | int) -> None:
        self._resolve(request_id, "rejected")

    def _resolve(self, request_id: str | int, decision: str) -> None:
        key = str(request_id)
        self._pending.pop(key, None)
        self._decisions.append((key, decision))

    # ── Status ────────────────────────────────────────────────────────

    def context_usage_pct(self) -> float | None:
        """The scripted value, or ``None`` when the script measured none.

        ``None`` is a real answer here, distinct from ``0.0``.
        """
        return self._last_context_pct
