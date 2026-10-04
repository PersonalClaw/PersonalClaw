"""SDK: what this PersonalClaw offers apps, by name (core features).

Every PersonalClaw built between two releases reads the same version, so ``minPersonalClawVersion``
cannot tell a build that has a contract from one made before it, and an app that relies on the newer
contract would install on the older build and quietly do less. A core feature names one contract an
app relies on, and a build offers the name exactly when it offers the contract.

* Declare the features your app needs in ``app.json``, by name:
  ``"requiresCoreFeatures": ["approval-answers"]``. A PersonalClaw that lacks one refuses to
  review, install, update or switch the app on, and says which feature it lacks.
* Ask :func:`core_has` about a feature your app can do without, and say so when it is missing rather
  than quietly doing less.

``APPROVAL_ANSWERS``: a channel's approval prompt offers the answers PersonalClaw hands it in the
approval brief (``personalclaw.sdk.channel.approval_brief_for(event)["answers"]``). A channel app
whose prompt offers them declares it.

``CHAT_TRUST``: a channel that runs a conversation itself offers that chat's Trust on its own
approval prompt and keeps none of its own (``personalclaw.sdk.channel.approval_brief_for(event,
chat=...)``, ``answer_in_chat`` and ``chat_grant``). A channel app that uses them declares it.

``CLOSING_STREAMS``: an app that reads a model's stream reads it inside
``personalclaw.sdk.model.closing_stream``, and the stream is closed the moment the app stops
reading it, by any way out; an agent CLI's turn left part way then tells the agent to stop and
gives its session back at once. An app that reads a model's stream declares it.

``DIGEST_REPLIES``: a channel that runs a conversation itself offers PersonalClaw the owner's
message before its own turn (``services.answer_channel_reply``), so the owner's answer to the
Morning triage digest that DM received is answered as the digest's card answers it. A channel app
that offers it declares it.

``GUARDED_DOWNLOAD``: a download streams through the egress guard
(``personalclaw.sdk.net.open_url``). An app that downloads with it declares it.

``LINKS_NAME_THEIR_CHANNEL``: a chat's link to a channel thread names the channel it is on, where
the chat answers (``link_channel(chat, thread, channel_id, provider=…)``,
``SessionManager.get_channel_provider``). A channel app that links a chat to one of its threads
declares it.

``TOOL_CALL_SCREEN``: a channel that runs a conversation itself asks the deny-list about each call
before it approves or asks about it (``personalclaw.sdk.channel.screen_tool_call``), and refuses a
call it refuses. A channel app that asks it declares it.

``TURNS_NAME_THEIR_CHANNEL``: a turn a channel writes for a conversation it runs itself names the
channel (``personalclaw.sdk.channel.save_conversation_turn(…, source_channel=…)``), a line it takes
into a chat itself records where it came from (``arrived_on(thread, sender, channel)``), and memory
takes a line as the owner's own words only when its sender is that channel's owner. A channel app
that saves its turns or imports a thread with them declares it.

``TURNS_NAME_WHO_ASKED``: a turn a channel runs itself names who asked for it
(``with personalclaw.sdk.channel.turn_asked_by(session_key, arrived_on(thread, sender, channel)):``
around the turn), and what its tools would change of the owner's memory waits for her own word
unless that sender is the owner. A channel app that runs a conversation's turns itself declares it.
"""

from personalclaw.apps.core_features import (
    APPROVAL_ANSWERS,
    CHAT_TRUST,
    CLOSING_STREAMS,
    CORE_FEATURES,
    DIGEST_REPLIES,
    GUARDED_DOWNLOAD,
    LINKS_NAME_THEIR_CHANNEL,
    TOOL_CALL_SCREEN,
    TURNS_NAME_THEIR_CHANNEL,
    TURNS_NAME_WHO_ASKED,
    core_has,
)

__all__ = [
    "APPROVAL_ANSWERS",
    "CHAT_TRUST",
    "CLOSING_STREAMS",
    "CORE_FEATURES",
    "DIGEST_REPLIES",
    "GUARDED_DOWNLOAD",
    "LINKS_NAME_THEIR_CHANNEL",
    "TOOL_CALL_SCREEN",
    "TURNS_NAME_THEIR_CHANNEL",
    "TURNS_NAME_WHO_ASKED",
    "core_has",
]
