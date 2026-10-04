"""Core features: the contracts this core offers apps, by name.

Every core built between two releases reads the same version (``personalclaw.__version__``), so a
version floor (``minPersonalClawVersion``) cannot tell a core that has a contract from one built
before it: an app that relies on the newer contract installs on the older core and quietly does
less. A feature name can. The change that adds a contract an app relies on adds its name here, so a
core offers the name exactly when it offers the contract.

Names, not a contract number: changes made side by side each add their own name and land as the
union of them, where two of them raising one number would land as one number for two contracts.
And a name says, in the refusal the owner reads, what the app needs.

An app declares the names it needs in ``app.json`` (``requiresCoreFeatures``), and a core that
lacks one refuses to review, install, update or switch the app on, naming it
(:func:`personalclaw.apps.core_version.check_core_compatibility`). An app asks :func:`core_has`
(``personalclaw.sdk.features``) about a feature it can do without, and says so when it is missing.

A name is a published surface: once it ships it stays, and so does what it promises
(``tests/test_core_features.py`` holds each name to its contract). Stdlib only, so the manifest
parser and the Store's catalog scans read it without importing the rest of core.
"""

from __future__ import annotations

import re

#: A channel's approval prompt offers the answers core hands it in the approval brief,
#: ``approval_brief_for(event)["answers"]`` (``channel_delivery.ApprovalAnswer``: Allow once and
#: Deny, and Allow for this chat when the prompt is asked in the chat that asks), and a press
#: resolves the prompt with the pressed answer's ``key``. A core without it sends a brief with no
#: answers, so a prompt that offers the brief's answers has nothing to offer.
APPROVAL_ANSWERS = "approval-answers"

#: A channel that runs a conversation itself offers core the owner's message before its own turn
#: (``GatewayServices.answer_channel_reply(provider, msg, is_dm=…)``): core takes the owner's
#: answer to the Morning triage digest that DM received (``3 yes``), answers it as the digest's card
#: does, says in the DM what it did, and returns True, so the channel runs no turn for it; anything
#: else returns False and stays the channel's. A core without it has no such method on the services
#: handle, so an app that calls it fails on every direct message.
DIGEST_REPLIES = "digest-replies"

#: An app can stream a download through the egress guard: ``personalclaw.sdk.net.open_url`` opens
#: a URL with every request it sends, each redirect hop included, asked of the guard first under
#: the connector policy and the owner's Network egress settings, and raises ``EgressBlocked`` for
#: a refused one before it is sent. A core without it has no ``open_url``, so an app that imports
#: it does not load.
GUARDED_DOWNLOAD = "guarded-download"

#: A channel that runs a conversation itself keeps no trust of its own: its approval prompt for a
#: call of that conversation offers what the chat's card offers, Allow for this chat included
#: (``approval_brief_for(event, chat=<session key>)``), ``answer_in_chat`` makes the pressed Allow
#: for this chat that chat's Trust in PersonalClaw (shown in the chat, and switched off there), and
#: ``chat_grant`` says who approves a later call without asking, by the decision the chat's runner
#: makes (an operator's hook pattern, what the call declares, the chat's grants, each held to the
#: allowed hosts and the operator ceiling), so the channel approves no call on its own. A core
#: without it has neither function, so an app that imports them does not load.
CHAT_TRUST = "chat-trust"

#: A channel that runs a conversation itself refuses a call the deny-list refuses before it
#: approves or asks about it: ``screen_tool_call(hooks, title, tool_input)`` is the hook chain's
#: verdict on the call, read on the command it would run (given as text or as a list of words) as
#: well as on its title, the one screen every approval path in core asks first. A core without it
#: has no ``screen_tool_call``, so an app that imports it does not load.
TOOL_CALL_SCREEN = "tool-call-screen"

#: A chat's link to a channel thread names the channel the thread is on, and that is where the
#: chat answers: ``link_channel(chat, thread, channel_id, provider=…)`` on the dashboard state a
#: channel is handed links a chat there (moving it off any thread it was on; the owner's own DM it
#: left is told where it went), and ``SessionManager.get_channel_provider(key)`` says which channel
#: a session is on. A core without it takes no ``provider``, so an app that links a chat with one
#: fails to link it.
LINKS_NAME_THEIR_CHANNEL = "links-name-their-channel"

#: A turn a channel writes for a conversation it runs itself names the channel it came on:
#: ``save_conversation_turn(log, key, user_text, assistant_text, source_thread=…, source_user=…,
#: source_channel=…)`` records the channel on each line, beside its thread and sender, and memory
#: takes a line as the owner's own words only when its sender is the owner that channel keeps
#: (``owner_id_for``), so another person in the conversation is never read as the owner. A line a
#: channel takes into a chat itself (a thread it imports) records the same with ``arrived_on(thread,
#: sender, channel)`` as its ``source``. A core without it takes no ``source_channel``, so a channel
#: app that names one fails to save its turns.
TURNS_NAME_THEIR_CHANNEL = "turns-name-their-channel"

#: A model's stream an app reads is closed the moment the app stops reading it:
#: ``personalclaw.sdk.model.closing_stream(events)`` reads *events* inside a block, and leaving the
#: block by any way out (the terminal event, a ``break``, an error, a cancel) closes the stream at
#: once, and with it every stream beneath it. An agent CLI's turn closed part way tells the agent
#: to stop and gives its session back, so the session's next prompt is sent as soon as the agent
#: has answered, never when the interpreter collects the stream. A core without it has no
#: ``closing_stream``, so an app that imports it does not load.
CLOSING_STREAMS = "closing-streams"

#: A turn a channel runs itself names who asked for it: ``with turn_asked_by(session_key,
#: arrived_on(thread, sender, channel)):`` around the turn says whose message it answers, and while
#: it runs what its tools would change of the owner's memory waits for her own word unless that
#: sender is the owner the channel keeps (``owner_id_for``), as for a turn the door hands a chat. A
#: core without it has no ``turn_asked_by``, so an app that imports it does not load.
TURNS_NAME_WHO_ASKED = "turns-name-who-asked"

#: A channel message runs once: the door takes each message once, by the channel's own id for it
#: in the chat it came in (``ChannelMessage.message_id`` with its ``channel_id``), and a delivery of
#: it made again (while its turn runs, after it finished or failed, after a restart) is answered
#: ``already_received`` and changes nothing; a message with no id is refused. A channel that acts on
#: a message itself before the door or instead of it claims it first
#: (``personalclaw.sdk.channel.claim_message(provider, msg)``: True the first time, False for a
#: delivery made again), and the door takes a message its channel claimed, once. A core without it
#: has no ``claim_message``, so an app that imports it does not load.
MESSAGES_RUN_ONCE = "messages-run-once"

#: Every feature this core offers. A name is added with its contract and never taken away.
CORE_FEATURES: frozenset[str] = frozenset(
    {
        APPROVAL_ANSWERS,
        CHAT_TRUST,
        CLOSING_STREAMS,
        DIGEST_REPLIES,
        GUARDED_DOWNLOAD,
        LINKS_NAME_THEIR_CHANNEL,
        MESSAGES_RUN_ONCE,
        TOOL_CALL_SCREEN,
        TURNS_NAME_THEIR_CHANNEL,
        TURNS_NAME_WHO_ASKED,
    }
)

#: The shape of a feature name: lowercase words joined by hyphens.
FEATURE_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


def core_has(feature: str) -> bool:
    """Whether this core offers *feature*, one of :data:`CORE_FEATURES`."""
    return feature in CORE_FEATURES
