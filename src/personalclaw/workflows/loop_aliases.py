"""Legacy loop-kind → workflow-template aliases, resolved at READ time.

Years of chat history, saved crons, hooks and user muscle memory refer to loops by kind:
"start a goal loop", `kind: research`. Those references do not stop existing when the
templates land, and a migration that rewrote them all would be a migration over data the
user cannot see to check.

So the aliases resolve at read time instead. A stored `kind: goal` keeps working because
lookup understands it, not because something rewrote it. That means **zero migration
code** for years-old references, and it means the aliases are deletable in one commit at
the Phase-4 endgame rather than being load-bearing forever.

**The direction is deliberately one-way.** A loop kind resolves to a template; a template
does not resolve back to a loop kind. Reverse lookup would invite writing new references
in the legacy vocabulary, and an alias layer that accepts new writes is not a bridge, it
is a second API.

**An unknown kind resolves to nothing rather than to a default.** Guessing a template for
an unrecognised identifier would silently run the wrong workflow — and "it ran something"
is far harder to debug than "it ran nothing and said why".
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Legacy loop kind → the template that replaces it.
#:
#: `goal` maps to the OPEN-ENDED variant, not the verifiable one: a bare "goal loop" with
#: no verify command is open-ended by definition, and mapping it to the verifiable variant
#: would demand an input the legacy reference never had.
#:
#: `code` maps to `code-project`, the R5 restructure of what shipped in Slice 9a as
#: `code-implementation` (WF2LOO-10). That was a product decision — EVOLVE the one code
#: template rather than ship a second one beside it — and this alias is why it needed no
#: migration: a stored `kind: code`, a saved cron and a two-year-old transcript all keep
#: resolving, at READ time, to whatever the current code template is called.
KIND_TO_TEMPLATE: dict[str, str] = {
    "general": "general-project",
    "goal": "goal-pursuit-open-ended",
    "code": "code-project",
    "design": "design-project",
    "research": "deep-research",
}

#: Legacy loop chat-tool names → the template a caller meant. Same read-time discipline:
#: a chat transcript from months ago says `loop_create_research`, and that phrase should
#: keep working without anything rewriting the transcript.
TOOL_TO_TEMPLATE: dict[str, str] = {
    "loop_create_general": "general-project",
    "loop_create_goal": "goal-pursuit-open-ended",
    "loop_create_code": "code-project",
    "loop_create_design": "design-project",
    "loop_create_research": "deep-research",
    "loop_start": "general-project",
}

#: Variant hints that refine a bare kind. A legacy `goal` loop carrying a verify command
#: WAS the verifiable variant in all but name, so the alias can honour that rather than
#: forcing the user to re-choose something they already expressed.
VARIANT_HINTS: dict[tuple[str, str], str] = {
    ("goal", "verifiable"): "goal-pursuit-verifiable",
    ("goal", "open_ended"): "goal-pursuit-open-ended",
    ("goal", "open-ended"): "goal-pursuit-open-ended",
}


def resolve_kind(kind: str, *, variant: str = "", has_verify_command: bool = False) -> str:
    """The template for a legacy loop kind, or "" if there is no alias.

    `has_verify_command` is read as a variant signal: a goal loop with a command that
    proves it is a verifiable goal whatever it was labelled, and honouring that is better
    than dropping the user into a template that ignores the command they supplied.
    """
    normalized = (kind or "").strip().lower()
    if not normalized:
        return ""

    if variant:
        hinted = VARIANT_HINTS.get((normalized, variant.strip().lower()))
        if hinted:
            return hinted
    if normalized == "goal" and has_verify_command:
        return "goal-pursuit-verifiable"

    template = KIND_TO_TEMPLATE.get(normalized)
    if template is None:
        # Deliberately no default. Running the wrong workflow is harder to debug than
        # running none, because "it ran something" hides the mistake.
        logger.debug("no template alias for loop kind %r", kind)
        return ""
    return template


def resolve_tool(tool_name: str) -> str:
    """The template a legacy loop chat-tool name meant, or ""."""
    return TOOL_TO_TEMPLATE.get((tool_name or "").strip().lower(), "")


def aliased_kinds() -> list[str]:
    """Every legacy kind that still resolves. Shrinks to empty at the endgame."""
    return sorted(KIND_TO_TEMPLATE)


def alias_manifest() -> dict[str, object]:
    """The alias table, for the API and for a deprecation report.

    Exposed so the endgame can be planned from data — "which legacy vocabulary is still
    being used" is answerable from usage against this table, and a deprecation nobody can
    measure is a deprecation that never happens.
    """
    return {
        "kinds": dict(KIND_TO_TEMPLATE),
        "tools": dict(TOOL_TO_TEMPLATE),
        "variants": {f"{k}:{v}": t for (k, v), t in VARIANT_HINTS.items()},
        "one_way": True,
        "note": (
            "Read-time aliases for legacy loop references. Deleted wholesale at the "
            "Phase-4 endgame; never written to."
        ),
    }


# ── Cockpit live-follow key equivalence (R10c) ──
#
# The loop cockpit keys per-loop SSE on `loop:<id>`. A template run streams under a
# run-scoped key. Strict-equality matching between the two DROPS events silently, which
# is a proven FE regression class — the stream connects, the cockpit renders, and nothing
# updates, with no error anywhere.

#: The prefixes a container key can carry. Ordered longest-first so `workflow:run:` is
#: matched before `workflow:`, which a shorter-first scan would truncate.
_KEY_PREFIXES = ("workflow:run:", "workflow:", "loop:", "run:")


def base_container(key: str) -> str:
    """Strip any stream-key prefix down to the bare container id.

    The point is that `loop:abc`, `run:abc` and `workflow:run:abc` are all the SAME
    container. A cockpit comparing raw keys with `==` sees three different things and
    updates on none of them.
    """
    raw = (key or "").strip()
    for prefix in _KEY_PREFIXES:
        if raw.startswith(prefix):
            return raw[len(prefix) :]
    return raw


def keys_equivalent(left: str, right: str) -> bool:
    """Do these two stream keys name the same container?

    Used wherever a cockpit decides "is this event mine". Empty keys are never
    equivalent — treating two blanks as a match would route every unkeyed event to every
    open cockpit.
    """
    left_base = base_container(left)
    right_base = base_container(right)
    if not left_base or not right_base:
        return False
    return left_base == right_base
