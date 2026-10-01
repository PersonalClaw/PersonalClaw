"""A tool call's arguments, read against the input schema its tool declares.

The native runtime answers a call that is missing an argument its tool requires before anyone is
asked to approve it (`NativeAgentRuntime._preflight`): approving it could run nothing, and the
identical retry would ask again.

Only a missing required argument is refused here, because it is the one failure every tool that
declares the schema refuses. The rest is the tool's to judge, before the ask when it declares the
check (`ToolProvider.preflight`) and when it runs: a built-in tool takes an object where it
declares JSON text and reads ``"5"`` as a number. A tool whose schema IS its validator, an MCP
server's, declares the types its schema refuses (:func:`mistyped_arguments`, judged on the
arguments as the MCP client sends them). The schema read is the one the tool DECLARED, never the
portable copy a model request carries (:mod:`personalclaw.tool_providers.portable_schema`), which
may name fewer properties required.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The keywords the checks run: the ones that reach a nested value along a path every instance of
#: the schema must satisfy, and the one keyword each check asks about (``required``, ``type``).
#: Every other keyword is skipped, so the schema's own patterns are never run here (one written to
#: backtrack would stall the gateway on a model's argument), and a branch of ``anyOf``/``oneOf``
#: another branch may satisfy is not held against the call.
_REACH = frozenset({"$ref", "$dynamicRef", "allOf", "properties", "items", "prefixItems"})
_MAX_PROBLEMS = 5
_MAX_PROBLEM_CHARS = 200
_MAX_SCHEMA_CHARS = 2000


def _skip(validator: Any, value: Any, instance: Any, schema: Any) -> None:
    return None


def missing_arguments(arguments: Any, input_schema: Any) -> list[str]:
    """Each argument *input_schema* requires that *arguments* lack, one line each; ``[]`` if none.

    ``[]`` too when there is no schema, or one that cannot be checked (it is not a valid schema,
    or it points at a document this process will not fetch): the call then goes on as it would
    have, to be asked about and to be checked by the tool itself.
    """
    return _problems(arguments, input_schema, "required")


def mistyped_arguments(arguments: Any, input_schema: Any) -> list[str]:
    """Each argument whose JSON type is not one *input_schema* declares for it, one line each with
    where it is; ``[]`` if none, and for a schema that cannot be checked. Only ``type`` is asked:
    what a value's pattern, enum or format allows stays the tool's to say."""
    return _problems(arguments, input_schema, "type")


def _problems(arguments: Any, input_schema: Any, keyword: str) -> list[str]:
    """What *arguments* fail of *input_schema*'s *keyword*, along the paths every instance must
    satisfy (``_REACH``)."""
    if not isinstance(input_schema, dict) or not input_schema:
        return []
    import jsonschema

    try:
        base = jsonschema.validators.validator_for(
            input_schema, default=jsonschema.validators.Draft202012Validator
        )
        base.check_schema(input_schema)
        checked = _REACH | {keyword}
        skipped = {name: _skip for name in base.VALIDATORS if name not in checked}
        checker = jsonschema.validators.extend(base, skipped)(input_schema)
        errors = [e for e in checker.iter_errors(arguments) if e.validator == keyword]
    except Exception:  # noqa: BLE001 - a schema that cannot be read refuses nothing
        logger.debug("tool input schema could not be checked; leaving the call to its tool")
        return []
    problems: list[str] = []
    for error in sorted(errors, key=lambda e: [str(p) for p in e.absolute_path])[:_MAX_PROBLEMS]:
        where = "/".join(str(p) for p in error.absolute_path)
        line = f"{where}: {error.message}" if where else error.message
        problems.append(line[:_MAX_PROBLEM_CHARS])
    return problems


def missing_arguments_note(tool_name: str, problems: list[str], schema: Any) -> str:
    """Why a call missing what its schema requires was not run (a :class:`ToolResult` error):
    what is missing, and the schema itself, so the model can correct the call without another
    lookup."""
    return _note(
        tool_name,
        "its call lacks what its input schema requires.",
        problems,
        schema,
        "Call it again with every required argument.",
    )


def mistyped_arguments_note(tool_name: str, problems: list[str], schema: Any) -> str:
    """Why a call with an argument of a type its schema does not declare was not run (a
    :class:`ToolResult` error): which, where, and the schema itself."""
    return _note(
        tool_name,
        "an argument is not of the type its input schema declares.",
        problems,
        schema,
        "Call it again with each argument of the type its schema declares.",
    )


def _note(tool_name: str, why: str, problems: list[str], schema: Any, again: str) -> str:
    lines = [f"{tool_name} was not run: {why}"]
    lines += [f"- {p}" for p in problems]
    shown = json.dumps(schema) if isinstance(schema, dict) else ""
    if shown and len(shown) <= _MAX_SCHEMA_CHARS:
        lines.append(f"Its input schema: {shown}")
    else:
        lines.append(f'Call tool_schema("{tool_name}") to see its input schema.')
    lines.append(again)
    return "\n".join(lines)
