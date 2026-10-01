"""The one transform a tool's name takes on the wire: the model-safe form every provider accepts.

Converse, OpenAI function calling and the Messages API each take a tool name of letters, digits,
``_`` and ``-`` only, at most 64 characters on Converse and OpenAI and 128 on the Messages API. A
tool's real name need not be one: an external MCP tool is ``mcp/<server>/<tool>``, and a request
carrying it fails as a whole. So every request names each tool by its model-safe form
(:func:`model_safe_name`), and dispatch maps the form a call names back to the real tool
(:func:`build_sanitized_index`). The real name stays the tool's identity everywhere else (the
dispatch index, approvals, the Tools page). The wire map is ``docs/architecture/tool-name-wire.md``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

logger = logging.getLogger(__name__)

_TOOL_NAME_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]")
_TOOL_NAME_SANITIZE_MAX = 64


def model_safe_name(name: str) -> str:
    """The name a request carries for the tool called ``name``: each character outside
    ``[a-zA-Z0-9_-]`` becomes ``_``, cut to 64. A name already in that form is its own."""
    safe = _TOOL_NAME_SANITIZE_RE.sub("_", name or "")[:_TOOL_NAME_SANITIZE_MAX]
    return safe or "tool"


def build_sanitized_index(names: Iterable[str]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """The model-safe(real)->real dispatch map for a tool census, plus the forms it cannot route.

    Each real name whose model-safe form differs from it maps that form back to it, but ONLY when
    no other tool of the census shares the form: two tools a call names the same way cannot be told
    apart, and dispatching a guess would run the wrong tool.

    Returns ``(healing_map, collisions)``. ``collisions`` maps each shared form to every real name
    that travels as it, sorted, a real name that IS the form included. A request may carry a form
    once, so of those only the tool whose real name is the form (if one is) can be offered; the
    rest are left out of the request (:func:`left_out`). The census rail
    (``tests/test_tool_name_wire_fidelity.py``) keeps every shipped tool out of this branch.
    """
    by_form: dict[str, list[str]] = {}
    for real in set(names):
        by_form.setdefault(model_safe_name(real), []).append(real)
    healing: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}
    for form, reals in by_form.items():
        if len(reals) > 1:
            collisions[form] = sorted(reals)
        elif reals[0] != form:
            healing[form] = reals[0]
    return healing, collisions


def left_out(collisions: dict[str, list[str]]) -> dict[str, str]:
    """Each real name no request can carry, mapped to the form it shares: every name in a collision
    except the one that is the form itself."""
    return {real: form for form, reals in collisions.items() for real in reals if real != form}


def name_census(
    names: Iterable[str], *, taken: Iterable[str] = ()
) -> tuple[dict[str, str], set[str]]:
    """The dispatch map for the tools a runtime offers, and the tools it must leave out.

    ``taken`` are names the runtime answers itself (its meta-tools): a tool of that name, or one
    sent under it, could never be called, and a request carrying both would name a tool twice. Any
    other tool whose model-safe form another tool already has is left out too (:func:`left_out`).
    Each clash is logged once per census, naming the tools left out, so none disappears silently.
    """
    names, taken = list(names), set(taken)
    healing, collisions = build_sanitized_index([*names, *taken])
    unroutable = set(left_out(collisions))
    shadowed = sorted(n for n in names if n in taken)
    if shadowed:
        logger.warning(
            "native: tools %s are NOT offered to models: the runtime's own tools have those "
            "names; rename them",
            shadowed,
        )
        unroutable.update(shadowed)
    for form, reals in collisions.items():
        gone = [r for r in reals if r != form]
        if form in reals:
            logger.warning(
                "native: tools %s are NOT offered to models: a request has to name them %r, which "
                "is another tool's own name; rename them",
                gone,
                form,
            )
        else:
            logger.warning(
                "native: tools %s are NOT offered to models: a request has to name each of them "
                "%r, so a call to that name could not be told apart; rename one",
                gone,
                form,
            )
    return healing, unroutable
