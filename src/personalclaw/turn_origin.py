"""The keys a transcript row records where it came from, as the typed turn reads them: the fields
of its source, and the ``meta`` keys of a row several queued messages, an outside text or someone
else's request run as.

Kept below the gateway, which this imports nothing of, so the typed turn (``turns``) reads a row's
source without the channels, the trust rules and the owner's names that ``turn_source`` holds
beside them; ``turn_source`` re-exports every name here.
"""

from __future__ import annotations

#: The fields a row records where it came from: the thread it arrived on, who sent it, and the
#: chat channel that took it in.
SOURCE_FIELDS = ("source_thread", "source_user", "source_channel")

#: The ``meta`` key of a row several queued messages run as when they came from different places:
#: the source each of them records, in order, written by the chat's queue as it merges them. Such a
#: row records no source of its own (``turn_source.shared_source``), so this says whose they were.
QUEUED_FROM = "queued_from"

#: The ``meta`` key of a row whose words PersonalClaw took in from outside as they came, which no
#: model of the conversation wrote: a scheduled run's result opened as a chat
#: (``dashboard.schedule_inject``), a heartbeat task's result or a check-in written into a chat
#: (``gateway``'s ``_deliver_result``), and the list of saved prompts, as their files describe them
#: (``dashboard.chat_runner``'s ``/prompts``). It holds what a model is told they are and where they
#: came from (``what``, and the fence's ``source``, ``source_type`` and ``source_id``), and the
#: chat's own model is handed them through the door every text from outside takes into a prompt
#: (``turn_source.model_text``). The row keeps its words as a person reads them.
FROM_OUTSIDE = "from_outside"

#: The ``meta`` key of a row that starts a turn for work someone other than the owner asked for,
#: when no message of theirs is in it: the report of a subagent their turn started. It holds their
#: source (``memory_writes.asked_for_work``), which ``turn_source.asked_by`` reads, and a queued
#: message carries it under the same key until its row is written (``chat_queue``).
ASKED_FOR_BY = "asked_for_by"
