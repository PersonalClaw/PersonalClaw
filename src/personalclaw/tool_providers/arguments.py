"""A tool call's arguments, read against the input schema its tool declares.

The native runtime answers a call that is missing an argument its tool requires before anyone is
asked to approve it (`NativeAgentRuntime._refuse_missing_arguments`): approving it could run
nothing, and the identical retry would ask again.

Only a missing required argument is refused here, because it is the one failure every tool that
declares the schema refuses. The rest is the tool's to judge: a server built on a lax validator
takes ``"true"`` for a boolean and ``"5"`` for an integer, the MCP client turns a number sent as
text back into a number, and a built-in tool takes an object where it declares JSON text. The
schema read is the one the tool DECLARED, never the portable copy a model request carries
(:mod:`personalclaw.tool_providers.portable_schema`), which may name fewer properties required.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The keywords the check runs: the ones that reach a nested object's ``required`` along a path
#: every instance of the schema must satisfy. Every other keyword is skipped, so the schema's own
#: patterns are never run here (one written to backtrack would stall the gateway on a model's
#: argument), and a branch of ``anyOf``/``oneOf`` another branch may satisfy is not held against
#: the call.
_CHECKED = frozenset(
    {"$ref", "$dynamicRef", "allOf", "properties", "required", "items", "prefixItems"}
)
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
    if not isinstance(input_schema, dict) or not input_schema:
        return []
    import jsonschema

    try:
        base = jsonschema.validators.validator_for(
            input_schema, default=jsonschema.validators.Draft202012Validator
        )
        base.check_schema(input_schema)
        skipped = {keyword: _skip for keyword in base.VALIDATORS if keyword not in _CHECKED}
        checker = jsonschema.validators.extend(base, skipped)(input_schema)
        errors = [e for e in checker.iter_errors(arguments) if e.validator == "required"]
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
    """The answer to a call missing what its schema requires: what is missing, and the schema
    itself, so the model can correct the call without another lookup."""
    lines = [f"Error: {tool_name} was not run: its call lacks what its input schema requires."]
    lines += [f"- {p}" for p in problems]
    shown = json.dumps(schema) if isinstance(schema, dict) else ""
    if shown and len(shown) <= _MAX_SCHEMA_CHARS:
        lines.append(f"Its input schema: {shown}")
    else:
        lines.append(f'Call tool_schema("{tool_name}") to see its input schema.')
    lines.append("Call it again with every required argument.")
    return "\n".join(lines)
