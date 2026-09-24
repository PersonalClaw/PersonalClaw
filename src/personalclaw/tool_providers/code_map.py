"""The ``code_map`` tool — one call instead of three greps (CONTEXT-ECONOMY §5.5).

Finding a symbol today costs a `grep` for the name, a `read_file` or two for
context, and often another grep for callers. Each round-trip is tokens spent on
navigation rather than work. This tool answers the same question from the
tree-sitter index in one call.

Registered as ``workflows-tools`` so its group derives to ``workflows`` (per §5.1)
while keeping a distinct registry key — ``personalclaw-workflows`` is already taken,
and the registry is a dict keyed by provider name, so reusing it would silently
replace the workflows provider.

**Fail-soft, always.** No index, no parser, an empty workspace: the tool says so
plainly and names grep/read as the way through. It never fabricates and never
raises — a broken accelerator has to leave the agent exactly where it would have
been without it.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

logger = logging.getLogger(__name__)

# Answer budgets. The tool exists to SAVE context, so a response that dumps
# hundreds of rows defeats its own purpose.
_MAX_DEFINITIONS = 25
_MAX_REFERENCES = 40
_MAX_OUTPUT_CHARS = 12_000

_NO_INDEX_HINT = (
    "Use `grep` and `read_file` instead — they work regardless of whether the " "code index exists."
)


class CodeMapToolProvider(ToolProvider):
    """Query the codebase symbol index."""

    @property
    def name(self) -> str:
        return "workflows-tools"

    @property
    def display_name(self) -> str:
        return "PersonalClaw Code Map"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="code_map",
                description=(
                    "Look up where a symbol is defined and which files reference it, "
                    "or outline one file's imports and definitions — from a "
                    "pre-built index, in ONE call instead of several grep/read "
                    "round-trips. Prefer this over grep when you're navigating by "
                    "symbol or function name. Falls back to reporting no index (use "
                    "grep/read then); indexes Python, TypeScript, JavaScript, Rust "
                    "and Go."
                ),
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                parameters={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": (
                                "Function, class, method or type name to locate. "
                                "Returns its definition sites plus the files that "
                                "reference it."
                            ),
                        },
                        "file": {
                            "type": "string",
                            "description": (
                                "Outline this file instead: its imports and every "
                                "definition with line numbers. A workspace-relative "
                                "or trailing path fragment both work."
                            ),
                        },
                        "workspace": {
                            "type": "string",
                            "description": (
                                "Directory to query. Defaults to the active "
                                "workspace; you rarely need to set this."
                            ),
                        },
                        "refresh": {
                            "type": "boolean",
                            "description": (
                                "Re-index changed files before answering. The index "
                                "self-updates, so this is only for a tree you just "
                                "modified outside the session."
                            ),
                        },
                    },
                },
            ),
            ToolDefinition(
                name="code_map_overview",
                description=(
                    "The codebase's shape: the most-referenced modules and their "
                    "public surface, with line numbers. Read this once when you're "
                    "new to a repository instead of exploring file by file."
                ),
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                parameters={
                    "type": "object",
                    "properties": {
                        "workspace": {
                            "type": "string",
                            "description": "Directory to summarize (defaults to the active one).",
                        },
                    },
                },
            ),
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            if tool_name == "code_map":
                return await _run(_code_map, arguments)
            if tool_name == "code_map_overview":
                return await _run(_code_map_overview, arguments)
        except Exception as exc:  # noqa: BLE001 — never raise into the agent loop
            logger.debug("code_map: %s failed", tool_name, exc_info=True)
            return ToolResult(
                success=False,
                error=f"The code index could not answer that ({exc}).",
                recovery_hints=[_NO_INDEX_HINT],
            )
        return ToolResult(success=False, error=f"Unknown tool: {tool_name}")


async def _run(fn, arguments: dict) -> ToolResult:
    """Run a blocking index query off the event loop."""
    import asyncio

    return await asyncio.get_event_loop().run_in_executor(None, fn, arguments)


def resolve_workspace(arguments: dict) -> str:
    """Which directory to query: the explicit argument, else the active workspace."""
    explicit = str(arguments.get("workspace") or "").strip()
    if explicit:
        return explicit
    try:
        from personalclaw.config.loader import default_workspace_dir

        return default_workspace_dir() or ""
    except Exception:  # noqa: BLE001
        return ""


def _open_index(arguments: dict):
    """``(index, error_result)`` — exactly one is non-None."""
    workspace = resolve_workspace(arguments)
    if not workspace:
        return None, ToolResult(
            success=False,
            error=(
                "WHAT: No workspace directory is set, so there's nothing to index.\n"
                "WHY: code_map queries a per-workspace symbol index and none is "
                "resolvable here.\n"
                "FIX: Pass `workspace` explicitly, or set the workspace in Settings."
            ),
            recovery_hints=[_NO_INDEX_HINT],
        )
    from personalclaw.codegraph import CodeGraphIndex

    index = CodeGraphIndex(workspace)
    refresh = bool(arguments.get("refresh"))
    if refresh or index.is_empty():
        stats = index.index()
        if index.is_empty():
            return None, ToolResult(
                success=False,
                error=(
                    f"WHAT: No indexable source files were found under {workspace}.\n"
                    "WHY: code_map indexes Python, TypeScript, JavaScript, Rust and "
                    "Go; this tree has none, or they're all inside skipped "
                    "directories (node_modules, .venv, build…).\n"
                    "FIX: Navigate with grep/read instead."
                ),
                recovery_hints=[_NO_INDEX_HINT],
                metadata={"index": stats.to_dict()},
            )
    return index, None


def _code_map(arguments: dict) -> ToolResult:
    symbol = str(arguments.get("symbol") or "").strip()
    file_arg = str(arguments.get("file") or "").strip()
    if not symbol and not file_arg:
        return ToolResult(
            success=False,
            error="Pass either `symbol` (to locate a name) or `file` (to outline one file).",
        )

    index, failure = _open_index(arguments)
    if failure is not None:
        return failure
    assert index is not None

    if file_arg:
        outline = index.file_outline(file_arg)
        if not outline:
            return ToolResult(
                success=False,
                error=(
                    f"'{file_arg}' isn't in the index. It may be a language this tool "
                    "doesn't index, inside a skipped directory, or simply not exist."
                ),
                recovery_hints=[_NO_INDEX_HINT],
            )
        return ToolResult(success=True, output=_render_outline(outline))

    definitions = index.definitions_of(symbol, limit=_MAX_DEFINITIONS)
    references = index.references_to(symbol, limit=_MAX_REFERENCES)
    if not definitions and not references:
        return ToolResult(
            success=True,
            output=(
                f"No definition or reference to '{symbol}' in the index.\n"
                "It may be defined in a language this tool doesn't index, be a local "
                "variable (only declarations are indexed), or not exist. `grep` will "
                "confirm."
            ),
        )
    text = _render_symbol(symbol, definitions, references)
    return ToolResult(
        success=True,
        output=text[:_MAX_OUTPUT_CHARS],
        metadata={"definitions": len(definitions), "references": len(references)},
    )


def _code_map_overview(arguments: dict) -> ToolResult:
    index, failure = _open_index(arguments)
    if failure is not None:
        return failure
    assert index is not None
    summary = index.module_summary()
    if not summary:
        return ToolResult(
            success=True,
            output="The index is empty — nothing to summarize yet.",
        )
    stats = index.stats()
    header = (
        f"{stats['files']} indexed files · {stats['definitions']} definitions · "
        f"{', '.join(f'{k} {v}' for k, v in sorted(stats['languages'].items()))}\n\n"
    )
    return ToolResult(success=True, output=(header + summary)[:_MAX_OUTPUT_CHARS])


def _render_symbol(symbol: str, definitions: list, references: list) -> str:
    lines = []
    if definitions:
        exact = [d for d in definitions if d["name"] == symbol]
        similar = [d for d in definitions if d["name"] != symbol]
        if exact:
            lines.append(f"DEFINED ({len(exact)}):")
            for d in exact:
                lines.append(f"  {d['path']}:{d['line']}  {_definition_label(d)}")
                if d["signature"]:
                    lines.append(f"      {d['signature'][:200]}")
        if similar:
            lines.append(f"\nSIMILAR NAMES ({len(similar)}):")
            for d in similar:
                lines.append(f"  {d['path']}:{d['line']}  {_definition_label(d)}")
    else:
        lines.append(f"No definition of '{symbol}' found (it may be imported or dynamic).")

    if references:
        by_file: dict[str, list[int]] = {}
        for ref in references:
            by_file.setdefault(ref["path"], []).append(ref["line"])
        lines.append(f"\nREFERENCED IN {len(by_file)} file(s):")
        for path, ref_lines in sorted(by_file.items()):
            shown = ", ".join(str(n) for n in ref_lines[:10])
            more = f" (+{len(ref_lines) - 10} more)" if len(ref_lines) > 10 else ""
            lines.append(f"  {path}:{shown}{more}")
        if len(references) >= _MAX_REFERENCES:
            lines.append(f"  … reference list capped at {_MAX_REFERENCES}; grep for the full set.")
    else:
        lines.append("\nNo references found outside its definition.")
    return "\n".join(lines)


def _definition_label(row: dict) -> str:
    owner = f"{row['owner']}." if row.get("owner") else ""
    return f"{row['kind']} {owner}{row['name']}"


def _render_outline(outline: dict) -> str:
    lines = [f"{outline['path']}  ({outline['language']})"]
    if outline["imports"]:
        lines.append("\nIMPORTS:")
        for statement in outline["imports"][:40]:
            lines.append(f"  {statement}")
    if outline["definitions"]:
        lines.append(f"\nDEFINITIONS ({len(outline['definitions'])}):")
        for d in outline["definitions"]:
            indent = "    " if d["owner"] else "  "
            lines.append(f"{indent}{d['line']:>5}  {_definition_label(d)}")
    else:
        lines.append("\nNo definitions found in this file.")
    return "\n".join(lines)[:_MAX_OUTPUT_CHARS]


def create_code_map_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the code-map tool surface."""
    return CodeMapToolProvider()
