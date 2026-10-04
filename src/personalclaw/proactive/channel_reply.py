"""A reply to the Morning triage digest, typed on the chat channel the digest reached.

The digest reaches the owner's DM on a chat channel when their rule for its notice sends it there
(the ``channel_dm`` target), and under it the DM says how to answer there: ``3 yes``, ``3 no``,
``always yes 3``, ``yes all`` (:func:`reply_footer`, the grammar's own help line). This module is
what makes those words true:

* **Where each digest went** (:func:`note_delivered`). When the digest's notice reaches a DM, the
  run it belongs to is recorded against that DM (``digest_channels.json``), so a reply there answers
  THAT digest and no other. A newer digest that reaches the same DM replaces it; one that reaches
  another channel leaves it, and a reply to the old one is then refused as expired by the answer
  path, never acted on against the newer digest's numbers.
* **What a reply is** (:func:`answer_on_channel`). The channel's owner (``owner_id_for``, the id
  its owner pairing stored), in the DM that digest reached, in the digest's grammar: an answer form
  and nothing else (``parse_reply``). Anything else is a message for the chat, as it always was: a
  stranger's ``3 yes``, a paired correspondent's, the owner's in a group, a ``help``, a sentence
  that happens to start with a number.
* **The answer** is the card's own (:func:`personalclaw.proactive.answer.answer`), given as you on
  that channel (``approval_answer.on_channel``), and what it did is said back in the DM.

A channel whose messages cross the guarded door (``channel_inbound.deliver_inbound``) is answered
there. A channel that runs its conversations itself (Slack) offers the owner's message first with
``GatewayServices.answer_channel_reply``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personalclaw.atomic_write import atomic_write

if TYPE_CHECKING:
    from personalclaw.channel_transports.base import ChannelMessage
    from personalclaw.proactive.answer import Answered

logger = logging.getLogger(__name__)

#: The notice meta key the digest's notice carries when a reply can answer it: the run whose
#: proposals a reply answers (`pipeline.make_notify_deliver`, only when some wait for an answer).
REPLY_ANSWERS_KEY = "reply_answers"

#: Which digest each chat channel's DM last received: ``{"channels": {provider: {"channel",
#: "run_id", "at"}}}``. One entry per channel, since the digest reaches the owner's one DM there.
_STORE_NAME = "digest_channels.json"


def _store_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _STORE_NAME


def _read() -> dict[str, dict[str, str]]:
    """The recorded deliveries, by channel. A missing or unreadable file is none: a reply then
    answers nothing and goes to the chat, the side on which nothing is acted on."""
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("digest replies: %s is unreadable; no reply answers a digest", _STORE_NAME)
        return {}
    channels = data.get("channels") if isinstance(data, dict) else None
    if not isinstance(channels, dict):
        return {}
    return {
        str(provider): {k: str(v) for k, v in entry.items()}
        for provider, entry in channels.items()
        if isinstance(entry, dict)
    }


def note_delivered(note: dict[str, Any], *, provider: str, channel: str) -> None:
    """Record that *note* reached the owner's DM *channel* on *provider*, when it is a digest a
    reply answers (it carries :data:`REPLY_ANSWERS_KEY`). Anything else records nothing. Never
    raises: the notice went out whatever this could write, and a reply there then reaches the chat
    as any message does."""
    run_id = str(note.get(REPLY_ANSWERS_KEY) or "")
    if not run_id or not provider or not channel:
        return
    try:
        channels = _read()
        channels[provider] = {
            "channel": channel,
            "run_id": run_id,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write(_store_path(), json.dumps({"version": 1, "channels": channels}, indent=2))
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning("digest replies: could not record the digest sent on %s", provider)


def digest_received(provider: str, channel: str) -> str:
    """The run whose digest the owner's DM *channel* on *provider* last received, or ``""``."""
    entry = _read().get(provider) or {}
    if not channel or entry.get("channel") != channel:
        return ""
    return str(entry.get("run_id") or "")


def reply_footer(note: dict[str, Any]) -> str:
    """How to answer *note* on the chat channel it reaches, or ``""`` for a note no reply answers.

    The digest's own text names its card, which is true wherever it is shown; a chat channel adds
    this, because there the reply is the answer."""
    if not note.get(REPLY_ANSWERS_KEY):
        return ""
    from personalclaw.proactive.approval import HELP_TEXT

    return HELP_TEXT


def _is_an_answer(text: str) -> bool:
    """Whether *text* is one of the digest's answer forms: an item's number (or ``all``) with yes
    or no, once or always. Its help form and anything it cannot read are not: they are the chat's.
    """
    from personalclaw.proactive.approval import ReplyAction, parse_reply

    return parse_reply(text).action not in (ReplyAction.HELP, ReplyAction.UNPARSEABLE)


def _memory(services: Any) -> Any:
    """The global memory's service, which an "always" teaches its rule in: the one the running
    gateway's context reads."""
    from personalclaw.memory_service import MemoryService

    builder = getattr(services, "ctx_builder", None)
    store = getattr(getattr(builder, "memory", None), "vector_store", None)
    if store is None:
        raise RuntimeError("no memory store is running here")
    return MemoryService.over_vector_store(store)


async def answer_on_channel(
    services: Any, provider: str, msg: "ChannelMessage", *, is_dm: bool = True
) -> bool:
    """Answer the digest with *msg* when it is the owner's reply to the digest its DM received.

    Returns True when it was one: the answer path ran (as you on *provider*) and what it did was
    said in the DM. False for anything else, which is the caller's to handle as before: a message
    in a group, from anyone but the channel's owner, in a DM that received no digest, or that is not
    one of the digest's answer forms.
    """
    from personalclaw import approval_answer
    from personalclaw.channel_delivery import channel_shown_as, same_user
    from personalclaw.config.credentials import owner_id_for
    from personalclaw.proactive import answer as triage_answer

    # The words first: most messages are not an answer, and reading them reads nothing else.
    if not is_dm or not _is_an_answer(msg.text or ""):
        return False
    if not same_user(owner_id_for(provider), str(msg.sender or "")):
        return False
    run_id = digest_received(provider, str(msg.channel_id or ""))
    if not run_id:
        return False
    you = approval_answer.on_channel(provider)
    done = await triage_answer.answer(
        run_id,
        msg.text,
        door=triage_answer.Door(
            by=you,
            caller=you.label,
            source="channel",
            taught_from=f"a reply on {channel_shown_as(provider)}",
            memory=lambda: _memory(services),
        ),
    )
    logger.info(
        "channel %s: the owner's reply answered the digest of run %s: %s",
        provider,
        run_id,
        done.outcome,
    )
    await _say(provider, msg, reply_text(done))
    return True


async def _say(provider: str, msg: "ChannelMessage", text: str) -> None:
    """Say *text* in the DM *msg* came from, through that channel's own handle (which masks it).
    A channel that is gone, or a send that fails, is logged: the answer stands either way, and
    its card says what it did."""
    from personalclaw.channel_delivery import delivery_for

    delivery = delivery_for(provider)
    if delivery is None or not text:
        logger.info(
            "channel %s: an answered digest reply could not be told: not connected", provider
        )
        return
    try:
        await delivery.deliver_text(str(msg.channel_id or ""), text, str(msg.thread_id or ""))
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning("channel %s: telling the owner what their reply did failed", provider)


def _subject(result: dict[str, Any]) -> str:
    """The item a result is about, as the digest lists it: its number, its kind, its title."""
    kind = str(result.get("action_type", "") or "")
    title = str(result.get("title", "") or "")
    about = f"{kind} — {title}" if kind and title else (kind or title)
    return f"{result.get('ordinal', '')}. {about}" if about else f"Item {result.get('ordinal', '')}"


def _result_line(result: dict[str, Any]) -> str:
    """One answered item, in a sentence: done, declined, not done and why, or answered before."""
    subject = _subject(result)
    if result.get("outcome") == "unknown":
        return f"{subject}: it is not waiting for an answer in this digest, so nothing was done."
    if result.get("outcome") == "already":
        return f"{subject}: {result.get('detail') or 'already answered'}, so nothing ran again."
    said = str(result.get("verb", "") or "")
    if result.get("executed"):
        line = f"{subject}: done."
    elif result.get("not_done"):
        line = f"{subject}: {result['not_done']}"
    else:
        line = f"{subject}: declined, so nothing was done."
    if said.startswith("always"):
        if result.get("rule_error"):
            line += f" Nothing was remembered: {result['rule_error']}."
        elif result.get("rule"):
            line += f" Remembered: {said} for proposals like this one."
    if result.get("recorded") is False:
        line += " The answer could not be recorded, so a second answer would act again."
    return line


def reply_text(done: "Answered") -> str:
    """What a reply did, said back in the DM it came from."""
    from personalclaw.proactive import answer as triage_answer

    if done.outcome == triage_answer.ACTED:
        return "\n".join(_result_line(result) for result in done.results)
    if done.outcome == triage_answer.HELP:
        return done.help_reason or done.help
    if done.outcome == triage_answer.EXPIRED:
        if done.current_run_id:
            return (
                "That digest has been replaced by a newer one, so its numbers no longer name the "
                "items it listed and nothing was done. Answer the newest Morning triage on its "
                "card in your Inbox."
            )
        return (
            "That digest can no longer be answered (Morning triage is off, or its run is gone), "
            "so nothing was done."
        )
    if done.outcome == triage_answer.UNREADABLE:
        return f"The digest could not be read, so nothing was done: {done.error}"
    return done.error


__all__ = [
    "REPLY_ANSWERS_KEY",
    "answer_on_channel",
    "digest_received",
    "note_delivered",
    "reply_footer",
    "reply_text",
]
