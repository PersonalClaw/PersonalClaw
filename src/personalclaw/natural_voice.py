"""Natural voice — an owner-facing control for plainer, less machine-sounding prose.

The owner can ask for prose that reads like a person wrote it without editing a
prompt. The control exists at two scopes:

* **per-conversation**, set from the chat composer, and
* **on an agent definition** (``AgentProfile.natural_voice`` /
  ``AgentDefinition.natural_voice``), so it travels with that agent wherever the
  agent is used.

The resolution order between them is stated exactly once, in
:data:`NATURAL_VOICE_PRECEDENCE`, and :func:`resolve` is a loop over that tuple.
Nothing else in the codebase — no docstring, no test, no frontend — restates it;
callers that need to explain the order to a user read the tuple.

Naming: the field is ``natural_voice`` and deliberately **not** ``voice``.
``AgentProfile.voice`` already means the persona ("WHO the agent is — tone,
opinions, bluntness, persona") and ``voice_profiles`` already means SPEECH
(MULTIMODAL-IO), so a third meaning on that one word would collide with two
shipped surfaces at once.

REJECTED ALTERNATIVE — a post-hoc rewriting pass
------------------------------------------------
The obvious other implementation is to let the model answer normally and then
send its answer back through a second call that rewrites it into plainer prose.
That was considered and rejected, for three reasons in increasing order of
seriousness:

1. It is slower. The user waits for two round trips to read one answer, and the
   second one cannot start until the first has finished streaming — so the
   latency is additive on exactly the surface where latency is felt most.
2. It costs twice. Every toggled-on turn pays a second full model call over the
   whole reply, for a change that is purely stylistic.
3. **It can change meaning.** A rewriting pass is a model editing text it did
   not reason about, with no access to the reasoning behind it. That is precisely
   the operation that drops a qualifier, softens a refusal into a hedge, or turns
   "this deletes the branch" into "this cleans up the branch". A style control
   must not be able to alter a fact or weaken a refusal, and a second model call
   over the output is a mechanism that can do both.

So the instruction is a **persona-layer instruction** instead, injected the same
way PT-1 injects a theme's persona: a bundled, user-editable prompt snippet
appended to the turn the model sees. One call, one meaning, and the model applies
the style while it composes rather than having it applied to it afterwards.
"""

from __future__ import annotations

import functools
import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)

# ─────────────────────────── the resolution order ───────────────────────────

#: The scopes that can state a natural-voice preference, MOST specific first.
#:
#: This tuple is the single statement of the resolution order. :func:`resolve`
#: walks it in order and returns the first scope that states a value, so the
#: order is not described anywhere — it is executed. Reordering this tuple
#: changes the behaviour, which is what makes the rails that read it real.
#:
#: * ``conversation`` — what the composer set for THIS conversation. It wins, and
#:   only for this conversation: it is stored on the session, never written back
#:   to the agent definition, so overriding an agent here never edits that agent.
#: * ``agent`` — what the bound agent's definition carries, so a preference
#:   travels with the agent into every conversation that binds it.
#: * ``platform`` — the shipped floor. Always states a value, which is what makes
#:   the walk total.
NATURAL_VOICE_PRECEDENCE: tuple[str, ...] = ("conversation", "agent", "platform")

#: The shipped floor: off. A style instruction nobody asked for is an
#: instruction the owner cannot attribute an output change to.
PLATFORM_DEFAULT: bool = False

#: The per-conversation control is a TRI-state, not a bool. ``""`` means "this
#: conversation states nothing, use the agent's" — and it has to be
#: distinguishable from ``off``, or a conversation could never turn the control
#: OFF for an agent whose definition turns it on. A closed map: the value arrives
#: from a client, and anything unrecognised reads as "states nothing".
_CONVERSATION_STATES: dict[str, bool] = {"on": True, "off": False}


class NaturalVoice(NamedTuple):
    """A resolved natural-voice decision and the scope that decided it.

    ``source`` is a member of :data:`NATURAL_VOICE_PRECEDENCE`. It is returned
    rather than inferred because the composer has to show WHICH scope is in
    charge — a toggle whose effect you cannot attribute is a toggle you cannot
    trust — and because the frontend must not re-derive the order to say so.
    """

    enabled: bool
    source: str


def normalize_conversation_choice(value: object) -> str:
    """Coerce a client-supplied per-conversation choice to ``""``/``"on"``/``"off"``.

    Anything outside the closed set (including ``None``, a number, or a typo)
    normalizes to ``""`` — "this conversation states nothing" — so a malformed
    request inherits the agent's preference instead of silently forcing one.
    """
    try:
        word = str(value or "").strip().lower()
    except Exception:  # noqa: BLE001 — an unstringable value is just an invalid one
        return ""
    return word if word in _CONVERSATION_STATES else ""


def resolve(conversation: object = "", agent: bool = False) -> NaturalVoice:
    """Resolve natural voice for one turn by walking :data:`NATURAL_VOICE_PRECEDENCE`.

    *conversation* is the per-conversation tri-state (see
    :func:`normalize_conversation_choice`); *agent* is the bound agent
    definition's ``natural_voice``.

    The agent scope states a value only when it is on. An agent definition
    carrying ``False`` is asking for the platform floor, which is already off, so
    treating it as "states nothing" costs no behaviour and keeps ``source``
    honest about who actually made the call.
    """
    stated: dict[str, bool | None] = {
        "conversation": _CONVERSATION_STATES.get(normalize_conversation_choice(conversation)),
        "agent": True if agent else None,
        "platform": PLATFORM_DEFAULT,
    }
    for source in NATURAL_VOICE_PRECEDENCE:
        value = stated.get(source)
        if value is not None:
            return NaturalVoice(enabled=value, source=source)
    # Unreachable: the last scope in the tuple is the floor, which always states.
    raise AssertionError(
        f"no scope in NATURAL_VOICE_PRECEDENCE={NATURAL_VOICE_PRECEDENCE!r} stated a value"
    )


def agent_default(agent_name: str) -> bool:
    """The ``natural_voice`` the named agent's definition carries.

    ``False`` for an unbound session, an unknown agent, or any read failure — the
    platform floor, never a guess. Reads the config agents map, which is the
    layer the chat runner binds.
    """
    name = str(agent_name or "").strip()
    if not name:
        return False
    try:
        from personalclaw.config.loader import AppConfig

        profile = AppConfig.load().agents.get(name)
        return bool(getattr(profile, "natural_voice", False))
    except Exception:
        logger.debug("natural_voice agent default lookup failed for %r", name, exc_info=True)
        return False


# ───────────────────── the instruction (PT-1's snippet path) ─────────────────────


@functools.lru_cache(maxsize=1)
def instruction() -> str:
    """The natural-voice instruction — the bundled ``natural-voice`` prompt snippet.

    Same path PT-1's personas take (``render_snippet_block`` over a bundled
    snippet, cached), so the text is editable in Settings → Prompts and there is
    exactly one persona-injection mechanism rather than two.
    """
    from personalclaw.prompt_providers.runtime import render_snippet_block

    return render_snippet_block("natural-voice")


def maybe_inject(message: str, conversation: object = "", agent: bool = False) -> str:
    """Append the natural-voice instruction to *message* when it resolves ON.

    Injected on EVERY turn it is on, not only a new session's first turn (which
    is what PT-1's persona does). Two reasons: the toggle is flippable
    mid-conversation, so a first-turn-only injection would make turning it on at
    turn 12 a visible no-op — the control would report itself enabled while doing
    nothing; and a style instruction is the first thing a long conversation
    forgets, unlike a fact.

    Returns *message* unchanged when it resolves off, and on any rendering
    failure — a style layer never blocks a turn.
    """
    if not resolve(conversation, agent).enabled:
        return message
    try:
        text = instruction()
    except Exception:
        logger.warning("Natural-voice instruction render failed", exc_info=True)
        return message
    if not text:
        return message
    return f"{message}\n\n{text}\n"
