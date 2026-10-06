"""A chore: one question PersonalClaw asks a model for itself, answered in a call of its own.

A chore is model work nobody typed: a chat's title and tags, its organize proposal, its follow-up
chips, its memory consolidation, the compression of a thread's history, the dashboard's
suggestions, a folder's icon, a channel thread's title. Each is one prompt and one answer, and
:func:`run_chore` is how every one of them reaches a model.

Each is a fresh call (``llm_helpers.one_shot_completion`` builds the model for the call and shuts
it down after it), so a chore is sent exactly its own prompt and nothing of any chore before it,
whichever chat that one was made for. The chores used to share one background session that the
session manager kept alive, and its conversation grew with every chore of every chat: a
consolidation was handed other chats' titles, organize questions and follow-ups as if they were its
conversation, and stored what it read there as the user's own facts.

The rest is a one-shot call's. The Background chain in Settings → Models answers it, each model in
turn when one fails, does not answer in time, is paused by its breaker, answers nothing or answers
in a shape the chore cannot read. The spend guard counts it against the day's ceiling. On a model
on this machine it waits its turn behind the calls somebody is waiting for. It writes one usage
row. And its model may use no tool: a chore answers in text from what its prompt carries, and that
prompt quotes chats, pages and messages nobody vetted. So a chore runs on a model, never on an
agent CLI (a whole agent with tools of its own, which a home may run every chat on), and a tool its
model asks for is refused, with nobody asked (``llm_helpers.one_shot_completion``).

A chore no model is chosen for is skipped: nothing is built and nothing is sent (the Background
chain is empty, the Chat chain it falls back to is too, and no configured model names one of its
own). :func:`run_chore` raises :class:`NoModelChosen` for it, and the surface it was for says so in
the words of :func:`needs_a_model`, which name where the model is chosen.

Three rules are a chore's own. Its prompt is masked (``security.redact_for_model``): no person
typed it, and every part of it was read out of stored text. What one answer may run to is bounded
on every model the chain walks, by the Background output limit the owner sets in Settings → Models
(``background.max_output_tokens``, read as each chore starts): measured without a bound, a
consolidation on a local model wrote 26,164 tokens over 1,224 s and answered nothing. And a chore
of an Incognito or Temporary chat reaches no model but the one that chat runs on
(:mod:`personalclaw.memory_writes`): each caller asks before it reads such a chat to a model, and
the helper refuses one that did not (:func:`run_chore`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from personalclaw import memory_writes
from personalclaw.agents.defaults import LITE_AGENT_NAME
from personalclaw.providers.provider_bridge import ProviderResolutionError
from personalclaw.security import redact_for_model
from personalclaw.usage_ledger import Attribution

if TYPE_CHECKING:
    from personalclaw.guardrails.failure import OutOfOutputRoom


class NoModelChosen(ProviderResolutionError):
    """A chore skipped because no model is chosen for it (:func:`model_chosen`): nothing was built
    and nothing was sent. The resolver's own refusal, so every reader of that one reads this as it
    did; a surface that shows why it has no answer says :func:`needs_a_model`."""


def model_chosen() -> bool:
    """Whether a model is chosen for the chores: the Background chain in Settings → Models, the
    Chat chain it falls back to, or a configured model that names one of its own. An agent CLI
    chooses none (``provider_bridge.model_chosen``): it runs no chore."""
    from personalclaw.providers.provider_bridge import model_chosen as chosen_for

    return chosen_for("background")


def needs_a_model(what: str) -> str:
    """What a surface says while no model is chosen for its chores, *what* naming them as she
    knows them ("Suggestions", "Follow-ups"): the one sentence each of those surfaces shows, naming
    where the model is chosen."""
    return f"{what} need a model: choose one in Settings → Models."


async def run_chore(
    prompt: str,
    *,
    usage: Attribution,
    validate: Callable[[str], str] | None = None,
    memory_mode: str | None = None,
) -> str:
    """The answer to the chore *prompt*: one fresh call on the Background chain, its spend
    recorded for *usage* (:func:`chore_usage`).

    *validate* reads the answer as the chore does: ``""`` for an answer it can use, else what is
    wrong with it. An answer that misses it is its model failing, and the chain's next model is
    asked; the last one is asked once more with what was wrong, and when that misses too an
    ``OutputContractError`` is raised. An empty answer is a failure on every chore. A chore no
    model answered raises the chain's failure, and ``owed_chores.no_model_answered`` says whether
    a later try can get past it. A chore no model is chosen for raises :class:`NoModelChosen`
    before anything is built.

    A chore of a chat that keeps nothing (Incognito or Temporary) reaches no model but the chat's
    own. That chat is the one the chore is for, which *usage* names, read by every record of its
    mode (*memory_mode* is the mode the caller holds for it), and the one the work the chore is
    made in derives from. In that chat's own turn, once the turn has named the model it runs on,
    the chore is the turn's work and runs on that model (``llm_helpers.one_shot_completion``).
    Anywhere else no model of the chat's is known, and the chore is refused with
    ``memory_writes.OtherModelRefused`` before anything is sent.
    """
    from personalclaw.config.loader import background_limits
    from personalclaw.llm_helpers import one_shot_completion

    if _reaches_no_model(usage.session_key, memory_mode):
        raise _refusal(usage.session_key, memory_mode)
    try:
        return await one_shot_completion(
            redact_for_model(prompt),
            use_case="background",
            usage=usage,
            validate=validate,
            max_output_tokens=int(background_limits().max_output_tokens),
        )
    except ProviderResolutionError as exc:
        # The resolver refused before building anything. When that is because no model is chosen
        # at all, the chore was skipped rather than failed, and its surface says so.
        if model_chosen():
            raise
        raise NoModelChosen(str(exc), exc.agent_error) from exc


def room_for(capped: OutOfOutputRoom) -> str:
    """What gives a chore whose model ran out of output room (*capped*) the room to finish, as a
    clause: the Background output limit every chore is held to (:func:`run_chore`), or the
    model's own when it stopped short of that one, which raising the Background limit does not
    reach."""
    from personalclaw.config.loader import background_limits

    if 0 < capped.output_cap < int(background_limits().max_output_tokens):
        return (
            "raise that model's own output limit where its provider's settings have one, or add "
            "another model to Background in Settings → Models"
        )
    return "raise the Background output limit in Settings → Models"


def _reaches_no_model(chat_key: str, memory_mode: str | None) -> bool:
    """Whether a chore for the chat *chat_key* (``""`` for none), made in the current work, may be
    sent to no model at all (:func:`run_chore`). Not in that chat's own turn once the turn has
    named its model (``memory_writes.own_model``), unless that is an agent CLI, which runs no
    chore: a chore naming another chat there is refused."""
    own = memory_writes.own_model()
    if (
        own
        and not memory_writes.is_agent_cli(own)
        and chat_key in ("", memory_writes.source_session())
    ):
        return False
    if not chat_key and memory_mode is None:
        return memory_writes.writes_refused()
    return memory_writes.blocks_background_models(chat_key, memory_mode=memory_mode)


def _refusal(chat_key: str, memory_mode: str | None) -> memory_writes.OtherModelRefused:
    """The refusal of a chore that reaches no model, naming the Background model it was not sent
    to, in the sentence every refused call says: about the chore's chat when that chat keeps
    nothing, else about the chat the work derives from."""
    from personalclaw.llm_helpers import use_case_chain

    chain = use_case_chain("background")
    model = chain[0] if chain else ""
    if chat_key and memory_writes.blocks_memory_writes(chat_key, memory_mode=memory_mode):
        with memory_writes.derived_from(chat_key, memory_mode=memory_mode):
            return memory_writes.OtherModelRefused(model)
    return memory_writes.OtherModelRefused(model)


def chore_usage(chat_key: str = "") -> Attribution:
    """Whose spend a chore is: background work by the lite agent, the built-in worker whose chores
    these are, for the chat it was made for (*chat_key*, the key that chat's own turns are
    recorded under) when there is one, so that chat's total holds it."""
    return Attribution(source="background", session_key=chat_key, agent=LITE_AGENT_NAME)
