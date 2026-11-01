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

``ACCOUNT_SETTINGS``: an app's settings come in two parts, a top-level ``settings`` schema for
the account every capability shares and each capability's own ``settingsSchema``, read with
``ProviderSettings.load(app, capability)`` and ``ProviderSettings.load_account(app)``
(``personalclaw.sdk.settings``). An app that declares ``settings`` declares it.

``ADAPTIVE_THINKING``: a model app on a wire of its own asks which models take adaptive thinking
(``personalclaw.sdk.model.thinks_adaptively``), by the rule PersonalClaw's own Anthropic-compatible
wire asks a model by. A model app that imports it declares it.

``APPROVAL_ANSWERS``: a channel's approval prompt offers the answers PersonalClaw hands it in the
approval brief (``personalclaw.sdk.channel.approval_brief_for(event)["answers"]``). A channel app
whose prompt offers them declares it.

``CAPABILITY_IDS``: each provider in an app's manifest may carry an ``id`` and a ``label``, and
PersonalClaw keeps it as its own capability, ``<app>/<id>``, so an app may declare one type of
provider more than once. An app that declares two providers of one type declares it.

``CHAT_TRUST``: a channel that runs a conversation itself offers that chat's Trust on its own
approval prompt and keeps none of its own (``personalclaw.sdk.channel.approval_brief_for(event,
chat=...)``, ``answer_in_chat`` and ``chat_grant``). A channel app that uses them declares it.

``CLOSING_STREAMS``: an app that reads a model's stream reads it inside
``personalclaw.sdk.model.closing_stream``, and the stream is closed the moment the app stops
reading it, by any way out; an agent CLI's turn left part way then tells the agent to stop and
gives its session back at once. An app that reads a model's stream declares it.

``CUT_OFF_ANSWERS``: a model app that reads its provider's own wire reads it through
``personalclaw.sdk.model.until_terminal``, so a stream that ends before the event its protocol ends
an answer with is cut off, never complete: the app emits no tool call or terminal event for it, and
the turn says the answer was cut off. A model app that reads its own wire declares it.

``DIGEST_REPLIES``: a channel that runs a conversation itself offers PersonalClaw the owner's
message before its own turn (``services.answer_channel_reply``), so the owner's answer to the
Morning triage digest that DM received is answered as the digest's card answers it. A channel app
that offers it declares it.

``GUARDED_CLIENTS``: an app sends its requests with HTTP clients the egress guard is inside
(``personalclaw.sdk.net``'s ``http_client``, ``sync_http_client`` and ``http_session``), each
asking the guard about every request it sends, or asks the guard itself (``RequestGuard``) from
its own client library's hook before each request. An app that uses one declares it.

``GUARDED_DOWNLOAD``: a download streams through the egress guard
(``personalclaw.sdk.net.open_url``). An app that downloads with it declares it.

``LINKS_NAME_THEIR_CHANNEL``: a chat's link to a channel thread names the channel it is on, where
the chat answers (``link_channel(chat, thread, channel_id, provider=…)``,
``SessionManager.get_channel_provider``). A channel app that links a chat to one of its threads
declares it.

``MESSAGE_INSTRUCTIONS``: an Inbox source hands PersonalClaw the owner's instruction for a message
beside its words (``personalclaw.sdk.inbox.IncomingMessage.instruction``), word for word as a
setting of the app declared an instruction (``x-meta.instruction``) holds it, and a fire on the
message hands its action the instruction outside any fence, then the message fenced once. An app
whose source hands one over declares it.

``MESSAGES_RUN_ONCE``: the door takes each channel message once, by the channel's own id for it in
its chat, and a delivery of it made again changes nothing; a channel that acts on a message itself
before the door or instead of it claims it first (``personalclaw.sdk.channel.claim_message``). A
channel app that claims its messages declares it.

``MODEL_EFFORTS``: a model app declares the reasoning-effort levels each of its models takes
(``ModelInfo(efforts=…)``, ``register_branded_app(spec, efforts_of=…)`` or a curated row's
``efforts``), and the composer offers a chat on that model exactly those; a model its app says
nothing about declares what PersonalClaw's own wire sends it
(``personalclaw.sdk.model.wire_efforts``). A model app that declares levels declares it.

``PAIRED_OWNER``: a channel keeps no owner but the one its owner pairing named
(``personalclaw.sdk.channel.paired_owner``), has PersonalClaw forget any other owner it holds for
the channel (``forget_owner``), and answers the code that paired its owner in PersonalClaw's words
(``CANNED_OWNER_PAIRED_REPLY``). A channel app that keeps only a paired owner declares it.

``PER_CALL_OUTPUT_CAP``: a model provider that declares ``takes_output_cap`` is sent ``max_tokens``,
an output cap for one request, in its ``complete``, and holds it to its own configured cap and its
model's ceiling (``personalclaw.sdk.model.output_cap`` lets the configured cap win). PersonalClaw
sends one once in a turn, on the request after a tool call was cut at the output limit, at twice
what that call stopped at. A model app whose provider takes one declares it.

``PRE_TOOL_HOOKS``: a channel that runs a conversation itself asks the operator's blocking hooks
about each call past the deny-list and before it approves or asks about it
(``personalclaw.sdk.channel.ask_pre_tool_hooks``), and refuses a call they refuse. A channel app
that asks them declares it.

``RUNNING_LIST``: the live work is one list, ``GET /api/running``, each row saying what its work is
doing, and a ``running_changed`` frame says it moved. An app that reads the list declares it, with
``/api/running`` in its manifest.

``THINKING_OUTPUT_CAP``: a model app asks the output cap a request to a model sends when neither
the operator nor the call set one (``personalclaw.sdk.model.default_output_cap``): 32,000 for a
model that thinks adaptively, whose thinking is spent out of the same cap, and 4,096 for any other,
never above a ceiling the app knows. A model app whose provider sends it declares it.

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

``WAITING_LIST``: everything waiting on the owner is one list, ``GET /api/waiting``, every kind with
its brief and the route it is answered through, and a ``waiting_changed`` frame says it moved. An
app that reads the list declares it, with ``/api/waiting`` in its manifest.
"""

from personalclaw.apps.core_features import (
    ACCOUNT_SETTINGS,
    ADAPTIVE_THINKING,
    APPROVAL_ANSWERS,
    CAPABILITY_IDS,
    CHAT_TRUST,
    CLOSING_STREAMS,
    CORE_FEATURES,
    CUT_OFF_ANSWERS,
    DIGEST_REPLIES,
    GUARDED_CLIENTS,
    GUARDED_DOWNLOAD,
    LINKS_NAME_THEIR_CHANNEL,
    MESSAGE_INSTRUCTIONS,
    MESSAGES_RUN_ONCE,
    MODEL_EFFORTS,
    PAIRED_OWNER,
    PER_CALL_OUTPUT_CAP,
    PRE_TOOL_HOOKS,
    RUNNING_LIST,
    THINKING_OUTPUT_CAP,
    TOOL_CALL_SCREEN,
    TURNS_NAME_THEIR_CHANNEL,
    TURNS_NAME_WHO_ASKED,
    WAITING_LIST,
    core_has,
)

__all__ = [
    "ACCOUNT_SETTINGS",
    "ADAPTIVE_THINKING",
    "APPROVAL_ANSWERS",
    "CAPABILITY_IDS",
    "CHAT_TRUST",
    "CLOSING_STREAMS",
    "CORE_FEATURES",
    "CUT_OFF_ANSWERS",
    "DIGEST_REPLIES",
    "GUARDED_CLIENTS",
    "GUARDED_DOWNLOAD",
    "LINKS_NAME_THEIR_CHANNEL",
    "MESSAGE_INSTRUCTIONS",
    "MESSAGES_RUN_ONCE",
    "MODEL_EFFORTS",
    "PAIRED_OWNER",
    "PER_CALL_OUTPUT_CAP",
    "PRE_TOOL_HOOKS",
    "RUNNING_LIST",
    "THINKING_OUTPUT_CAP",
    "TOOL_CALL_SCREEN",
    "TURNS_NAME_THEIR_CHANNEL",
    "TURNS_NAME_WHO_ASKED",
    "WAITING_LIST",
    "core_has",
]
