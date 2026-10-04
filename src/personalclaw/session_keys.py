"""Every kind of session key the gateway mints, and whether anybody watches the work it names.

A session key says whose work something is: a chat's turn, a subagent's tool call, a trigger's
fire, a Home tile's refresh, an app's request. Two questions are asked of it on the paths that
gate work, and both are answered here:

* **Is anybody watching?** The safety profile a run resolves (``guardrails.policy``), the
  approval posture of a run nobody can be asked in, the dollar caps an unattended image, video,
  speech or transcription is held to, the check that refuses an unattended command that would
  stop PersonalClaw, and the autonomy ladder's ceiling all read the one answer
  :func:`is_unattended` gives.
* **Is its runtime reset after each use?** A key of a stateless kind never resumes a provider
  session (``session.SessionManager``).

Each kind is one row below, and the code that mints a key reads its row (:meth:`SessionKind.key`,
:attr:`SessionKind.prefix`), so a key cannot be minted under one spelling and judged by a copy of
another. A key that matches no row is a chat's own name, one the owner or a client gave it, and a
chat is watched. ``tests/test_session_key_census.py`` reads the source tree for every key it mints
and fails on a kind no row names, so new work cannot be judged watched by omission.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionKind:
    """One kind of session key: its prefix and what the work under it is.

    ``unattended`` — nobody watches the work, so nobody can answer an ask it raises.
    ``stateless`` — its runtime is never resumed: every use starts fresh.
    ``wrapped`` — also judged in the ``dashboard:`` form the provider layer wraps a dashboard
    chat's key in, for a kind whose work runs as a dashboard chat but is never watched there.
    ``who`` — who is running, as the subject of a sentence a person reads ("A subagent asked to
    run …").
    """

    prefix: str
    unattended: bool
    who: str
    stateless: bool = False
    wrapped: bool = False

    def key(self, rest: str) -> str:
        """The key of this kind named *rest*."""
        return f"{self.prefix}{rest}"

    def names(self, key: str) -> bool:
        """Whether *key* is of this kind."""
        return key.startswith(self.prefix)


# ── watched work ──────────────────────────────────────────────────────────────────────────────

#: A chat opened in the dashboard, named ``chat-<n>-<time>`` (``dashboard.chat_names``).
CHAT = SessionKind("chat-", unattended=False, who="A chat")

#: The chat a scheduled job's results are shown in, ``cron-<job>``, which the owner opens from the
#: job (``dashboard.schedule_inject``). The job's own runs are :data:`TRIGGER`'s.
SCHEDULE_CHAT = SessionKind("cron-", unattended=False, who="A scheduled job's chat")

#: The form the provider, history and usage layers key a dashboard chat by
#: (``constants.dashboard_session_key``), and :data:`DASHBOARD_UI`.
DASHBOARD = SessionKind("dashboard:", unattended=False, who="A chat")

#: The dashboard's own pages: the owner at the dashboard, in no chat.
DASHBOARD_UI = DASHBOARD.key("ui")

#: A dashboard chat named by its transcript's file form, ``dashboard_<name>``
#: (``constants.DASHBOARD_FILE_PREFIX``).
CHAT_FILE = SessionKind("dashboard_", unattended=False, who="A chat")

#: A room member's own session, ``room:<room>:<member>`` (``rooms.turn``). The person in the room
#: approves every action its members take: that is what a room is.
ROOM = SessionKind("room:", unattended=False, who="A room member")

#: A workflow step's own session, ``workflow:<run>:<node>`` (``workflows.ownership``). Who answers
#: a step's gates is its run's to say (a run started by hand asks whoever started it,
#: ``workflows.gate_policy``), and what a run dispatches with nobody to ask is judged under
#: :data:`UNATTENDED` (``unattended:workflow:<run>``).
WORKFLOW_STEP = SessionKind("workflow:", unattended=False, who="A workflow step")

#: A rewrite the owner asked for from the composer, in a session of its own that ends with its
#: answer (``dashboard.handlers.optimizer``).
OPTIMIZER = SessionKind("_optimizer:", unattended=False, who="The prompt optimizer")

#: A test chat the owner started from an agent's page, in a session of its own that ends with its
#: reply (``dashboard.handlers.agent_marketplace``).
AGENT_TEST = SessionKind("agent_marketplace_test:", unattended=False, who="An agent's test chat")

# ── work nobody watches ───────────────────────────────────────────────────────────────────────

#: A trigger's fire or a scheduled job's run, ``cron:<id>`` (``triggers.wakeup``), a heartbeat
#: task's (``cron:system:heartbeat-tasks:<run>``), a scheduled script's calls back, and the run a
#: scheduled job's chat links to.
TRIGGER = SessionKind("cron:", unattended=True, stateless=True, who="A scheduled automation")

#: A subagent's own session, ``subagent:<id>`` (``subagent.agent_work_id``).
SUBAGENT = SessionKind("subagent:", unattended=True, stateless=True, who="A subagent")

#: A chat channel's own run. Never resumed.
CHANNEL = SessionKind("channel:", unattended=True, stateless=True, who="A channel's run")

#: An Inbox run. Never resumed.
INBOX = SessionKind("inbox:", unattended=True, stateless=True, who="An Inbox run")

#: A side question asked beside a chat, ``side:<chat>`` (``dashboard.side``): answered in a
#: throwaway session with every tool refused, so nothing in it is ever put to a person.
SIDE = SessionKind("side:", unattended=True, stateless=True, who="A side question")

#: A loop's worker, planner or task worker, ``loop-<id>`` (``loop.manager``). A loop's own
#: sessions also carry its Mode, which the chat runner reads first: an Attended loop's worker puts
#: its asks to a person.
LOOP = SessionKind("loop-", unattended=True, who="A loop")

#: A loop, in the colon spelling. Kept so a key named this way is judged as a loop's is.
LOOP_COLON = SessionKind("loop:", unattended=True, who="A loop")

#: A dispatch that has no session at all — a trigger's fire, a hook's action, a workflow's own
#: commands, a loop's gate — named for what fired it
#: (``guardrails.policy.unattended_dispatch_key``).
UNATTENDED = SessionKind("unattended:", unattended=True, who="An unattended run")

#: A caller reaching in from outside the dashboard: ``inbound:<surface>:<client>`` for the HTTP
#: dialects, ``inbound:cli:<name>`` for a headless ``personalclaw run`` (``inbound``, ``cli_run``).
#: Its turn runs as a dashboard chat, so its wrapped provider key is judged too.
INBOUND = SessionKind(
    "inbound:", unattended=True, wrapped=True, who="A caller from outside the dashboard"
)

#: A webhook's agent turn, ``hook:<id>`` (``dashboard.handlers.hooks``): an outside system starts
#: it, and nobody watches it run.
WEBHOOK = SessionKind("hook:", unattended=True, who="A webhook")

#: A Home tile's refresh, ``tile:<tile>`` (``dashboard.tile_refresh``). Its data sources run when
#: its time is up, and a refresh button runs the same refresh: what they fetch was written into
#: the tile, not asked for by anyone watching.
TILE = SessionKind("tile:", unattended=True, who="A Home tile's refresh")

#: An app's own work, ``app:<app>``: a request an app's backend makes with its token
#: (``dashboard.memory_write_gate``), a tool it invokes (``dashboard.handlers.tools``) and the
#: background agents it starts (``dashboard.handlers.apps``). The app's code makes the request,
#: not a person.
APP = SessionKind("app:", unattended=True, who="An app")

#: A workflow judge's own session, ``judge:<run>:<node>:<epoch>`` (``workflows.verify``).
JUDGE = SessionKind("judge:", unattended=True, who="A workflow's judge")

#: An evaluation run's session, ``eval_<session>_<time>`` (``eval.runner``): every turn is scripted.
EVAL = SessionKind("eval_", unattended=True, who="An evaluation run")

#: Every kind. A key is of the kind whose prefix is the longest it starts with (:func:`kind_of`).
KINDS: tuple[SessionKind, ...] = (
    CHAT,
    SCHEDULE_CHAT,
    DASHBOARD,
    CHAT_FILE,
    ROOM,
    WORKFLOW_STEP,
    OPTIMIZER,
    AGENT_TEST,
    TRIGGER,
    SUBAGENT,
    CHANNEL,
    INBOX,
    SIDE,
    LOOP,
    LOOP_COLON,
    UNATTENDED,
    INBOUND,
    WEBHOOK,
    TILE,
    APP,
    JUDGE,
    EVAL,
)

#: The prefixes whose runtime is never resumed (``session.SessionManager``).
STATELESS_PREFIXES: tuple[str, ...] = tuple(k.prefix for k in KINDS if k.stateless)


def kind_of(key: str) -> SessionKind | None:
    """The kind *key* is: the row whose prefix it starts with, the longest when two do. ``None``
    for a chat's own name, which no row names."""
    found: SessionKind | None = None
    for kind in KINDS:
        if kind.names(key) and (found is None or len(kind.prefix) > len(found.prefix)):
            found = kind
    return found


def judged_kind(key: str) -> SessionKind | None:
    """The kind the work *key* names is judged as: :func:`kind_of`, except that the dashboard's
    wrapped form of a :attr:`~SessionKind.wrapped` kind's key is judged as that kind, so the
    answer does not depend on which layer asks."""
    key = key or ""
    if DASHBOARD.names(key):
        inner = kind_of(key[len(DASHBOARD.prefix) :])
        if inner is not None and inner.wrapped:
            return inner
    return kind_of(key)


def is_unattended(key: str) -> bool:
    """Whether nobody watches the work *key* names (:func:`judged_kind`). A chat's own name, which
    no row names, is watched."""
    kind = judged_kind(key)
    return kind is not None and kind.unattended


def is_stateless(key: str) -> bool:
    """Whether *key*'s runtime is never resumed (:data:`STATELESS_PREFIXES`)."""
    return key.startswith(STATELESS_PREFIXES)
