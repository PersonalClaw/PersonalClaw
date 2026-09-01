"""Tool discovery + invocation for the native loop.

Two responsibilities:

1. **Discovery → model schema.** :func:`tool_definitions_to_openai_schema` converts
   the provider-neutral :class:`~personalclaw.tool_providers.base.ToolDefinition`
   list into the OpenAI ``tools`` array the :class:`ModelProvider.complete` contract
   expects. (OpenAI-shaped is the canonical wire format; the Anthropic provider
   re-maps it internally.)

2. **In-process MCP tools.** :class:`InProcessMcpToolProvider` exposes an MCP
   tool module's surface (``_list_tools`` / ``_call_tool`` — e.g.
   ``personalclaw-core`` via ``mcp_core``, ``personalclaw-automation`` via
   ``mcp_schedule``) directly in-process — *without* spawning the MCP server
   subprocess an external ACP CLI would. This is E2-P4's "option A"
   (fast, no subprocess); a real in-process MCP stdio *client* (option B,
   ``mcp_client.py``) can replace it later behind the same ``ToolProvider`` seam.

``_call_tool`` is synchronous (it returns a JSON/text string); it is run in a
thread executor so a slow tool never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

logger = logging.getLogger(__name__)


def tool_definitions_to_openai_schema(tools: list[ToolDefinition]) -> list[dict]:
    """Convert ``ToolDefinition``s into the OpenAI ``tools`` array shape.

    Each becomes ``{"type": "function", "function": {name, description,
    parameters}}``. ``parameters`` defaults to an empty-object schema when the
    tool declares none (some endpoints reject a missing schema).
    """
    schema: list[dict] = []
    for t in tools:
        params = t.parameters or {"type": "object", "properties": {}}
        schema.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": params,
                },
            }
        )
    return schema


class InProcessMcpToolProvider(ToolProvider):
    """Expose an in-process MCP tool module's surface directly.

    Wraps a module's ``_list_tools`` / ``_call_tool`` (the same handlers the MCP
    server would run as a subprocess) so a caller gets the full toolset with no
    subprocess and no JSON-RPC hop. Parameterized by the module path + provider
    name so both ``personalclaw-core`` and ``personalclaw-automation`` reuse it.
    """

    def __init__(
        self,
        *,
        module: str = "personalclaw.mcp_core",
        provider_name: str = "personalclaw-core",
        display: str = "PersonalClaw Core",
    ) -> None:
        self._tools: list[ToolDefinition] | None = None
        self._module = module
        self._provider_name = provider_name
        self._display = display

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def display_name(self) -> str:
        return self._display

    def _import_module(self):
        import importlib

        return importlib.import_module(self._module)

    async def list_tools(self) -> list[ToolDefinition]:
        if self._tools is not None:
            return self._tools
        _list_tools = self._import_module()._list_tools

        from personalclaw.task_modes import infer_risk_from_name
        from personalclaw.tool_providers.base import RiskLevel

        raw = await asyncio.get_event_loop().run_in_executor(None, _list_tools)
        defs: list[ToolDefinition] = []
        for tool in raw or []:
            params = (
                tool.get("inputSchema")
                or tool.get("input_schema")
                or tool.get("parameters")
                or {"type": "object", "properties": {}}
            )
            name = str(tool.get("name", ""))
            # These dict-defined tools carry no risk_level, so classify by name
            # (artifact_delete → destructive, automation_create/notify → caution,
            # *_list/*_get → safe). An explicit "risk_level" in the tool dict wins,
            # so a module can override the inference. Feeds both the approval gate
            # (via the runtime's risk map) and the Tools-page indicator.
            declared = str(tool.get("risk_level", "")).lower()
            risk = (
                declared
                if declared in ("safe", "caution", "destructive")
                else infer_risk_from_name(name)
            )
            defs.append(
                ToolDefinition(
                    name=name,
                    description=str(tool.get("description", "")),
                    provider=self.name,
                    parameters=params,
                    # The native loop's approval gate decides per-call; the core
                    # tools self-enforce deny-list/sensitive-path internally too.
                    requires_approval=True,
                    risk_level=RiskLevel(risk),
                )
            )
        self._tools = defs
        return defs

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        import contextvars

        _call_tool = self._import_module()._call_tool

        try:
            # _call_tool is sync (validation + logging + dispatch → str). Run it
            # off the event loop so a slow tool doesn't stall token streaming.
            # run_in_executor does NOT copy contextvars into the worker thread, so
            # propagate the current context explicitly — otherwise the session-key
            # binding the runtime set (for spawn parent-trust inheritance) is lost.
            ctx = contextvars.copy_context()
            output = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ctx.run(_call_tool, tool_name, arguments)
            )
            return ToolResult(success=True, output=output or "")
        except Exception as exc:  # noqa: BLE001 - surface any tool error to the model
            logger.debug("in-process tool %s failed: %s", tool_name, exc, exc_info=True)
            return ToolResult(success=False, error=str(exc))


def format_tool_result(result: ToolResult) -> str:
    """Render a ``ToolResult`` as the string fed back into the model's context.

    Surfaces the structured result contract (the tool-* ports) so the model can
    act on it: a truncation notice on success (so it can paginate/narrow), and
    machine-actionable ``recovery_hints`` on failure (so it adapts instead of
    guessing). Without this, hints a tool carefully populated would be silently
    dropped at the model boundary.
    """
    if result.success:
        out = result.output or ""
        if result.truncated and result.original_length is not None:
            out += (
                f"\n[output truncated — showing part of {result.original_length} "
                "chars; narrow the query or request the specific portion you need]"
            )
        return out
    # PLATFORM-LEGIBILITY §2: a WHAT/WHY/FIX envelope IS the failure message — its
    # render() supplies the labeled lines the model recovers from. Recovery hints
    # (the pre-envelope carrier) still append for results that don't carry one.
    parts: list[str] = []
    if result.agent_error is not None:
        parts.append(result.agent_error.render())
    else:
        parts.append(f"Error: {result.error}" if result.error else "Error: tool failed")
    for hint in result.recovery_hints:
        parts.append(f"Hint: {hint}")
    return "\n".join(parts)


#: What :func:`read_tool_arguments` returns when a non-empty argument string could not be read at
#: all. Distinct from ``{}``, which is the honest answer for a call that genuinely took no
#: arguments — collapsing the two is what made a truncated call indistinguishable from an empty one.
ARGUMENTS_UNREADABLE = object()


def read_tool_arguments(raw: Any) -> Any:
    """A model's tool-call arguments, or :data:`ARGUMENTS_UNREADABLE`.

    Providers emit ``tool_input`` as a raw JSON string (OpenAI streams argument fragments), so a
    response cut at ``max_tokens`` mid-call yields a prefix of valid JSON. That used to become
    ``{}``, and the tool then reported a MISSING ARGUMENT for a call the model had made correctly —
    the failure was misattributed, with no retry, no counter and no event, so it was invisible in
    the ledger and in any transcript review (issue 1773).

    Two tolerances before giving up, both of them real provider behaviour rather than speculation:

    * **markdown fences** — the non-agentic path already strips them (``llm_helpers._parse_llm``),
      so the agent path was behind our own standard for the same input.
    * **double-serialised arguments** — a JSON *string* whose content is itself the JSON object.
      Parsing once yields a string, which the old code discarded as "not a dict".
    """
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    if not isinstance(raw, str):
        return ARGUMENTS_UNREADABLE
    text = raw.strip()
    if text.startswith("```"):
        # ```json\n{...}\n``` → the body. Rsplit so a fence inside a string value is not a cut.
        body = text.split("\n", 1)[1] if "\n" in text else ""
        text = body.rsplit("```", 1)[0].strip() if "```" in body else body.strip()
    if not text:
        return ARGUMENTS_UNREADABLE
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return ARGUMENTS_UNREADABLE
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, str):
        # Double-serialised: the value IS the JSON document.
        try:
            inner = json.loads(parsed)
        except (json.JSONDecodeError, ValueError):
            return ARGUMENTS_UNREADABLE
        return inner if isinstance(inner, dict) else ARGUMENTS_UNREADABLE
    return ARGUMENTS_UNREADABLE
