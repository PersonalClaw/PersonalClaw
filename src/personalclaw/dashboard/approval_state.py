"""The pending-approval registry, mixed into :class:`~personalclaw.dashboard.state.DashboardState`.

A tool call waiting on a human decision (from a chat, a subagent, a workflow stage, or an MCP
server's elicitation) is ONE entry in ``_pending_approvals``, and every surface reads that entry
(``docs/architecture/inbox-channels.md`` §"Pending approvals"). This module holds the whole of an
approval's life, so the invariants that keep the surfaces in agreement live in one place: one
registration (:meth:`DashboardApprovalState._hold_approval`), one decision path
(:meth:`DashboardApprovalState.decide_session_approval`), and one withdrawal
(:meth:`DashboardApprovalState.withdraw_approval`) for every way an approval ends, whether
answered, expired, or cancelled because the work that asked for it ended first.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from personalclaw import approval_answer
from personalclaw.approval_answer import AnswerRefused, Principal
from personalclaw.approval_brief import RISK_LABELS, derive_blast_radius
from personalclaw.channel_delivery import APPROVAL_ENDINGS
from personalclaw.config import loader as config_loader
from personalclaw.constants import DASHBOARD_SESSION_PREFIX
from personalclaw.run_bounds import reach_note
from personalclaw.security import redact_field
from personalclaw.sel import sel
from personalclaw.task_modes import read_call, tool_input_to_str
from personalclaw.textfmt import clip_words

if TYPE_CHECKING:
    from personalclaw.channel_delivery import ApprovalAnswer
    from personalclaw.dashboard.state import _ChatSession

logger = logging.getLogger(__name__)


def _mark_permission_resolved(messages: list[dict], request_id: str, decision: str) -> None:
    """Persist a resolved decision into a permission message's cls JSON.

    The ONE writer of ``cls["resolved"]``. A near-identical ``Session.mark_permission_resolved``
    method sat beside it until #683 — same role filter, same ``request_id`` match, same
    assignment — with two differences that both cut the wrong way: it defaulted
    ``decision="approved"``, so a caller that forgot the argument silently recorded consent
    on a field that is the permanent record of a security decision, and it walked
    ``self.messages`` FORWARD where this walks ``reversed``, so on a session holding two
    permission rows for one ``request_id`` the two names resolved different rows. It had
    zero production callers (only tests, which is what made it read as live) and is deleted
    rather than kept as a wrapper: ``decision`` is positional and required here precisely so
    that no writer of this field can decline to name it.
    """
    for msg in reversed(messages):
        if msg.get("role") == "permission":
            try:
                cls = json.loads(msg.get("cls", "{}"))
                if cls.get("request_id") == request_id:
                    cls["resolved"] = decision
                    msg["cls"] = json.dumps(cls)
                    return
            except (json.JSONDecodeError, TypeError):
                pass


#: The in-chat card's closed decision vocabulary — must stay in step with the frontend's
#: ApproveAction union (web/src/pages/ChatPage.tsx). Past tense throughout: "approved"/"rejected",
#: NOT the "approve"/"reject" pair ``/api/approvals/{id}/{action}`` takes. Anything outside this
#: set is a 400 at the route, never a silent denial.
SESSION_APPROVAL_ACTIONS = frozenset(
    {"approved", "rejected", "trust", "trust_agent", "trust_reads", "yolo"}
)

#: How a pending approval ENDS — the ``outcome`` every ``approval_resolved`` frame carries. The
#: first two are a person's answer. The last two are an approval ending with NO answer:
#: ``expired`` — nobody answered inside its window; ``cancelled`` — the work that asked stopped
#: first (its chat turn, its subagent, its workflow run, its loop). Both fail closed, so the call
#: does not run, but neither is "denied": that word states a decision a person made, and the two
#: surfaces that used to say it for a stopped turn (the live card and, after a reload, a card
#: whose buttons could no longer deliver anything) were each telling the user something untrue.
#: The vocabulary is the channel contract's, so a channel's prompt is told the same four words.
APPROVAL_OUTCOMES = frozenset(APPROVAL_ENDINGS)
#: The two ways an approval ends without an answer. Also the two values a chat transcript's
#: permission row records for them, so a reload renders what happened instead of a live card.
UNANSWERED_OUTCOMES = frozenset({"expired", "cancelled"})

#: How a settled approval's Inbox row says it ended, by the outcome above: the verb of its
#: sentence ("It was denied.") and the word its title leads with ("Denied: bash"). Keyed by the
#: four outcomes the chat card renders too (approved, denied, and the two endings with no answer),
#: so the row and the card agree on what happened.
SETTLED_AS: dict[str, tuple[str, str]] = {
    "approved": ("was approved", "Approved"),
    "rejected": ("was denied", "Denied"),
    "expired": ("expired", "Expired"),
    "cancelled": ("was cancelled", "Cancelled"),
}

#: What a pending approval's Inbox row says in its title and its first line
#: (``_raise_approval_row``, :func:`_approval_row_body`), and how the first line reads once the
#: approval has ended (:func:`settled_row_text`).
_ASK_TITLE = "Approval needed: "
_ASKING = " is waiting for your decision on "
_ASKED = " asked for your decision on "


def chat_approval_id(session_key: str, request_id: str | int) -> str:
    """The registry id of an approval a chat is waiting on — derived here, and only here.

    A chat's ``request_id`` is unique only inside that chat: an ACP agent's permission request
    carries the agent's own JSON-RPC message id, which every connection counts from the same small
    integers, and the native runtime falls back to the tool NAME for a call with no id. A registry
    every surface reads cannot be keyed by that — two chats both waiting on ``"1"`` would share a
    row, and an answer given for one would land on the other. So the registry keys a chat approval
    by its session too, while the chat keeps addressing its own call by the bare ``request_id``
    its card, transcript and runner have always used.
    """
    return f"{session_key}:{request_id}"


def _who_asked(entry: dict[str, Any]) -> str:
    """Who is waiting on an approval, as the Inbox says it — the chat's agent and the chat, a
    loop's worker and the loop, a subagent of a chat, a session's own agent asking through the
    queue, or a background task. The one wording both of an approval's Inbox rows use (the ask,
    and the note it leaves when nobody answered)."""
    agent = str(entry.get("agent") or "")
    title = str(entry.get("session_title") or "")
    if agent:
        # Only a chat-held approval names its agent; that is how the two origins are told apart.
        # A loop's worker asks on the chat path too, and it is the loop the owner knows it by.
        from personalclaw.approval_source import loop_name_of

        loop_name = loop_name_of(str(entry.get("session") or ""))
        if loop_name is not None:
            return f"{agent} in the loop “{loop_name}”" if loop_name else f"{agent} in a loop"
        return f"{agent} in “{title}”" if title else f"{agent} in a chat"
    if entry.get("trigger"):
        # Work a trigger started (its action's agent): the trigger is what the owner knows it by,
        # and what the Inbox offers to run again.
        from personalclaw.auto_denials import trigger_asker

        return trigger_asker(str(entry.get("trigger_name") or ""))
    from personalclaw.workflows.ownership import parse_owned

    step = parse_owned(str(entry.get("session") or ""))
    if step is not None:
        # A workflow step's agent asks under its run's key: the step is named as the run page, and
        # the Inbox's "Run this step again", name it — by its label.
        return f"The “{_step_name(*step)}” step of a workflow run"
    from personalclaw.workflows.batch_start import asker

    if batch := asker(str(entry.get("id") or ""), title):
        # A batch waiting to start: what waits is the batch its chat's agent asked for, not a
        # subagent, which none of its tasks is yet.
        return batch
    session = str(entry.get("session") or "")
    if entry.get("source") == "subagent" and session.startswith("app:"):
        # An app's background agent (`SubagentInfo.app`), named by the app it works for.
        from personalclaw.apps.app_manager import display_name_of

        return f"A background agent of the app “{display_name_of(session[len('app:') :])}”"
    if entry.get("source") == "subagent":
        return f"A subagent of “{title}”" if title else "A subagent"
    if entry.get("source") == "agent":
        # A session's own agent asking for an Allow it cannot give itself (`owner_allow`).
        return f"The agent in “{title}”" if title else "An agent"
    from personalclaw.approval_source import ROOM_SOURCE, room_member_of

    if entry.get("source") == ROOM_SOURCE and (seat := room_member_of(session)) is not None:
        # A room member, named as the room's members panel names it, in the room it asked from.
        member, room = seat
        return f"{member} in the room “{room}”" if room else f"{member} in a room"
    return "A background task"


def _step_name(run_id: str, node_id: str) -> str:
    """A run's step as the run page names it: its label, else its id."""
    from personalclaw.workflows import store
    from personalclaw.workflows.models import Node, walk

    try:
        spec = store.read_spec(run_id) or {}
        for _path, node in walk(Node.from_dict(spec.get("root") or {})):
            if node.id == node_id:
                return node.label or node_id
    except Exception:  # noqa: BLE001 - a wording helper never fails the approval it describes
        logger.debug("could not read the steps of run %s", run_id, exc_info=True)
    return node_id


def _background_asker(*, source: str, session: str, trigger: str) -> str:
    """Who raised a background origin's approval, as the principal an answer is compared with
    (``approval_answer``, rule 2): the trigger whose run asked, the workflow run whose step asked,
    a room member by its own session, else the agent (a subagent, an MCP server's question) under
    its source."""
    if trigger:
        return approval_answer.trigger(trigger).label
    from personalclaw.workflows.ownership import parse_owned

    step = parse_owned(session)
    if step is not None:
        return approval_answer.run(step[0]).label
    from personalclaw.approval_source import ROOM_SOURCE

    if source == ROOM_SOURCE and session:
        return approval_answer.agent(session).label
    return approval_answer.agent(source or session).label


def _approval_row_body(entry: dict[str, Any]) -> str:
    """What a pending approval's Inbox row says. Server-composed product copy: every clause true.

    Names who is waiting (the chat's agent and the chat, or the background origin), on what, at
    what risk, and then what the call would actually do — the redacted arguments the listing
    carries — so the row can be judged from the Inbox rather than only opened.
    """
    tool = str(entry.get("tool") or "a tool")
    # The risk in the words every other surface uses for it (`approval_brief.RISK_LABELS`).
    risk = RISK_LABELS.get(str(entry.get("risk") or ""), "").lower()
    lines = [f"{_who_asked(entry)}{_ASKING}{tool}" + (f" (risk: {risk})." if risk else ".")]
    for detail in (entry.get("tool_purpose"), entry.get("tool_input")):
        text = clip_words(str(detail or ""), 200)
        if text:
            lines.append(text)
    return "\n".join(lines)


def settled_row_text(message: str, outcome: str, ended: str = "") -> str:
    """The text of an approval's Inbox row once the approval has ended *outcome* (one of
    :data:`APPROVAL_OUTCOMES`), from the text it asked with: its title says how it ended, its
    first line says who asked and that it ended so, and why when nobody answered (*ended*). What
    it was asked about stays as it was.

    From the row's own text rather than the registry's entry, so a row a previous process left
    asking reads settled too: the ask's title and first line are this module's
    (:func:`_approval_row_body`), and nothing else is changed.
    """
    verb, word = SETTLED_AS[outcome]
    title, gap, body = message.partition("\n\n")
    if title.startswith(_ASK_TITLE):
        title = f"{word}: {title[len(_ASK_TITLE):]}"
    first, newline, rest = body.partition("\n")
    if _ASKING in first:
        why = f": {ended}" if ended else ""
        first = f"{first.replace(_ASKING, _ASKED, 1)} It {verb}{why}."
    return f"{title}{gap}{first}{newline}{rest}"


def _restarted_text(message: str) -> str:
    """An approval's row a previous process left asking, once the restart has ended it."""
    from personalclaw.inbox import GATEWAY_RESTARTED

    return settled_row_text(message, "expired", GATEWAY_RESTARTED)


class DashboardApprovalState:
    """The pending-approval registry mixed into :class:`DashboardState`.

    ``DashboardState.__init__`` creates the registry and the background futures; the rest of
    the attributes below are the state's own, named here so the registry is type-checked
    against them.
    """

    _pending_approvals: dict[str, dict]
    _approval_futures: dict[str, asyncio.Future]  # type: ignore[type-arg]
    _sessions: dict[str, Any]
    _log: logging.Logger
    sessions: Any
    subagents: Any
    broadcast_ws: Callable[..., None]
    enable_yolo: Callable[..., None]
    push_sessions_update: Callable[[], None]
    channel_provider_for: Callable[[str], str]

    def approval_window_secs(self) -> float:
        """How long an approval waits for an answer: ``agent.approval_timeout_minutes`` (F-33).

        ONE window for every approval that waits — a chat's, a subagent's, an MCP server's
        question (which its own call ceiling cuts shorter). There used to be a second, five-minute
        window for "unattended" sources, keyed by a substring of ``source`` (``cron``, ``loop``,
        ``heartbeat``, ``schedule``, ``autonudge``), and no caller ever passed one: every production
        source is ``subagent`` or ``mcp:<server>``. It governed nothing, while reading as the rule
        for night-time work. What an unattended run really does is decline at once, without an
        approval at all (``chat_runner``'s fail-fast and the native runtime's own), because nobody
        is there to ask — and ``auto_denials.py`` now says so in the Inbox.

        Read per approval, so a change in Settings applies to the next one asked. An unreadable
        config falls back to the default window rather than failing the approval
        (``approval_grants.approval_window_secs``, which a workflow gate reads too).
        """
        from personalclaw.approval_grants import approval_window_secs

        return approval_window_secs()

    def waiting_on_owner(self, session: str) -> bool:
        """Whether a call *session* made is waiting on its owner's answer right now.

        True while a pending approval names the session: an ask of its own turn, or of a subagent
        it started. A loop's worker in that state is waiting on a person, not stuck, so neither the
        bound on its turn nor the loop's watchdog counts the wait against it; the approval window
        (:meth:`approval_window_secs`) is what bounds it.
        """
        key = str(session or "")
        return bool(key) and any(
            str(entry.get("session") or "") == key for entry in self._pending_approvals.values()
        )

    async def request_approval(
        self,
        approval_id: str,
        source: str,
        tool: str,
        *,
        tool_input: object = "",
        tool_purpose: str = "",
        session: str = "",
        trigger: str = "",
        asked_on_channel: bool = False,
        risk_level: str = "",
        tool_kind: str = "",
        annotations: dict[str, Any] | None = None,
        answered_alone: bool = False,
    ) -> bool:
        """Request interactive approval. Returns True if approved, False if rejected/timeout.

        ``answered_alone`` is an ask only a decision on it answers: a Trust or YOLO switch, which
        answers every pending approval it covers (``chat_handlers.api_chat_mode``), leaves it
        asking. A batch's consent to change things is one (``workflows.batch_start``).

        ``risk_level`` is what the tool behind the call DECLARES (``AgentEvent.risk_level``;
        ``""`` when nothing does). The pending row carries the call's effective risk from it,
        so the queue, its nudge and the phone describe the call from the declaration and the
        screened command, never from the tool's name. ``tool_kind`` is the kind the call
        arrived with (``AgentEvent.tool_kind``), which a chat's card reads the same call by: an
        ACP agent's shell call is known as one by its kind, so its command is screened here too.
        ``annotations`` is what the tool's server labels it (``AgentEvent.annotations``), which the
        pending row's blast radius shows as the server's word.

        ``asked_on_channel`` says the caller is already asking the owner on a chat channel (the
        gateway's race for a background origin), so no channel is asked a second time from here.

        ``trigger`` is the store id of the trigger whose run asked (its action's agent), or ``""``.
        It names the asker on every surface, and it is what lets the note an unanswered one leaves
        offer to run that trigger again (``auto_denials.note_expired``).

        Waits :meth:`approval_window_secs`. Timeout always fails closed to deny, and leaves a
        note in the Inbox saying so (:meth:`end_approval`).

        ``tool_input`` is ``object`` because that is what the approval path actually carries.
        It is ``AgentEvent.tool_input``, typed ``Any`` — the native loop puts a dict there and
        an ACP frame puts a pretty-printed string — and the gateway hands it straight over
        (``gateway.py`` ``_approve``, both call sites). Declaring ``str`` here did not make it
        one; it only hid the mismatch until a dict reached the redactor and raised
        ``TypeError: expected string or bytes-like object, got 'dict'`` out of
        ``security.scan_exfiltration_urls``, from inside the approval path, killing the
        subagent that was waiting on the answer.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        self._approval_futures[approval_id] = fut

        # 🔴 COERCED ONCE, HERE, BEFORE ANY REDACTION — the one boundary that mints the string a
        # human will read. The posture is deliberate and it is NOT a defensive `or ""`:
        #
        # · The scanner keeps its `str` contract and keeps raising on a non-`str`. A redactor that
        #   quietly accepted anything would let the string it SCANS diverge from the string a
        #   surface SHOWS, and a divergence there is how an unredacted secret reaches a user. A
        #   `TypeError` at the chokepoint is a loud bug report about a caller; it is the right
        #   behaviour, and what was wrong was that the approval path could produce one.
        # · Nothing is skipped. `tool_input_to_str` JSON-encodes a dict, so every URL and
        #   credential inside a structured argument is now scanned — strictly more than before,
        #   never less. Wrapping the call in `except` would have converted a dead subagent into a
        #   silently skipped exfiltration scan, which is worse than the crash.
        # · One implementation, shared with the chat card's `input_preview`
        #   (`chat_runner`/`chat_utils`), so the approval prompt and the tool pill cannot describe
        #   one call differently.
        display_input = tool_input_to_str(tool_input)
        # ONE reading of the RAW call gives the verdict, the risk and what it can touch, so no
        # surface can describe it differently. Read off the RAW `tool_input`, NOT
        # `display_input`: the entry's copy has had URLs and credentials rewritten, and screening
        # a string the shell will never see is how a verdict stops describing the actual call.
        reading = read_call(risk_level, tool, tool_kind, tool_input)

        entry = self._approval_entry(
            approval_id,
            request_id=approval_id,
            source=source,
            tool=tool,
            tool_input=display_input,
            tool_purpose=tool_purpose,
            session=session,
            trigger=trigger,
            asked_by=_background_asker(source=source, session=session, trigger=trigger),
            # Whether the call is established as a read, from the same owner and the same
            # inputs as the chat card's, so the two surfaces that ask a human for permission
            # cannot describe one call differently.
            is_read_only=reading.risk == "safe",
            blast_radius=derive_blast_radius(
                tool, risk=reading.risk, effects=reading.effects, annotations=annotations
            ),
            # From a declaration or a command read: a call that carries neither (an ACP agent's
            # own tool, an MCP server's question) has no risk anybody established, and "" says
            # exactly that.
            risk=reading.risk if risk_level or reading.effects is not None else "",
            reach=reach_note(risk_level, tool, tool_kind, tool_input, session),
        )
        if answered_alone:
            self.__dict__.setdefault("_answered_alone", set()).add(approval_id)
        if asked_on_channel:
            self.__dict__.setdefault("_channel_asked", set()).add(approval_id)
        timeout = self.approval_window_secs()
        # How this approval ends if nobody answers it. The waiter is the one party that knows
        # WHY it stopped waiting, so it says so: its window closing is `expired`; anything else
        # that ends the wait first — the subagent or run that owns it being cancelled, which
        # cancels this coroutine — is `cancelled`. A decision withdraws the approval itself,
        # which makes the `finally` below a no-op.
        timed_out = False
        try:
            # Published inside the try, so a waiter cancelled mid-publication still leaves
            # nothing listed: the finally ends whatever was registered.
            await self._hold_approval(entry)
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            # Fail closed: an unanswered prompt does not run. Audited as what it was — nobody
            # answered (`expired`), not a Deny: the audit log's Denied filter must not return a
            # refusal nobody made (the chat's row says the same, #3716).
            try:
                from personalclaw.sel import sel

                sel().log_api_access(
                    caller=f"approval_timeout:{source}",
                    operation="approval_timeout",
                    outcome="expired",
                    resources=f"tool={entry['tool'][:80]} after={int(timeout)}s",
                )
            except Exception:
                self._log.debug("SEL audit failed for approval timeout", exc_info=True)
            return False
        finally:
            self._approval_futures.pop(approval_id, None)
            self.end_approval(
                approval_id,
                outcome="expired" if timed_out else "cancelled",
                window_secs=timeout,
            )

    def answered_alone(self, approval_id: str) -> bool:
        """Whether only a decision on *approval_id* answers it (``request_approval``'s
        ``answered_alone``): a Trust or YOLO switch, which answers every pending approval it covers,
        leaves it asking."""
        return approval_id in self.__dict__.get("_answered_alone", set())

    async def hold_session_approval(
        self,
        session: "_ChatSession",
        request_id: str,
        *,
        tool: str,
        tool_input: str,
        tool_purpose: str,
        agent: str,
        risk: str,
        is_read_only: bool,
        blast_radius: dict[str, bool] | None,
        grant_agent: str,
        reach: str = "",
        deny_effect: str = "",
    ) -> None:
        """Publish the approval a chat's runner is about to wait on, under
        :func:`chat_approval_id`.

        The caller has ALREADY parked its future on ``session._approval_futures[request_id]``:
        this publishes it, and the publication is what makes it answerable from outside the chat,
        so an answer that arrives the instant it is listed must find the future in place.

        ``tool_input`` is the caller's already-sanitized display string (the same one the
        transcript row persists), and ``is_read_only`` whether the call is established as a
        read and ``blast_radius`` what it can touch, both read off the RAW input — the rule
        `request_approval` states, kept by the one caller that holds the raw object.
        ``deny_effect`` is what a Deny does when it does more than decline the call
        (``turn_endings.deny_effect``), "" when it declines it.
        """
        entry = self._approval_entry(
            chat_approval_id(session.key, request_id),
            request_id=request_id,
            # A chat approval names no source: that is the established convention every reader
            # keys "Another chat session" off (`useApprovalToasts`), and the chat is `session`.
            source="",
            tool=tool,
            tool_input=tool_input,
            tool_purpose=tool_purpose,
            session=session.key,
            is_read_only=is_read_only,
            blast_radius=blast_radius,
            agent=agent,
            risk=risk,
            grant_agent=grant_agent,
            reach=reach,
            deny_effect=deny_effect,
            asked_by=approval_answer.asker_of_chat(
                f"{DASHBOARD_SESSION_PREFIX}{session.key}", created_by_app=session.created_by_app
            ).label,
        )
        await self._hold_approval(entry)

    def _approval_entry(
        self,
        approval_id: str,
        *,
        request_id: str,
        source: str,
        tool: str,
        tool_input: str,
        tool_purpose: str,
        session: str,
        is_read_only: bool,
        blast_radius: dict[str, bool] | None,
        asked_by: str,
        agent: str = "",
        risk: str = "",
        grant_agent: str = "",
        trigger: str = "",
        reach: str = "",
        deny_effect: str = "",
    ) -> dict[str, Any]:
        """The ONE shape a pending approval has, whatever raised it.

        This dict is at once the ``GET /api/approvals`` row, the ``approval`` WS frame (the chat
        card, the out-of-context nudge, the phone queue) and the source of the Inbox row — so a
        field supplied here reaches every door, and no door can describe the call differently.

        Every LLM-sourced string is redacted here, once, for both origins. ``agent`` and
        ``grant_agent`` are known only to a chat and stay empty for a background origin: empty
        is "not known", never "none". ``risk`` is the call's effective risk, from what its tool
        declares, when it declares one. ``blast_radius`` is what the call can touch
        (``approval_brief.call_blast_radius``), or ``None`` when nothing was established.
        ``trigger`` is known only to a trigger's run, and its name
        is read once, here, so the ask and its note name it the same way. ``reach`` says why a
        call is asked about though a grant would answer any other: it reaches a host off the
        allowed hosts (``run_bounds.ask_note``). ``asked_by`` is the
        principal that raised it, which may never answer it (``approval_answer``, rule 2).
        ``source_label`` is where it came from in words
        (:func:`~personalclaw.approval_source.approval_source_label`). ``deny_effect`` is what a
        Deny does when it does more than decline the call (an agent CLI that can refuse it only
        by ending its turn), said on every card before it is pressed; "" when it declines it.
        """
        from personalclaw.approval_source import approval_source_label, live_chat_name
        from personalclaw.triggers.store import trigger_name

        title = redact_field(live_chat_name(self._sessions, session))
        named = redact_field(trigger_name(trigger)) if trigger else ""
        return {
            "id": approval_id,
            # How the WAITER addresses this call. Equal to `id` for a background origin; for a
            # chat it is the chat's own id, which the card posts back to the chat's approve route.
            "request_id": request_id,
            "source": source,
            "tool": redact_field(tool),
            "tool_input": redact_field(tool_input),
            "tool_purpose": redact_field(tool_purpose),
            "session": session,
            "session_title": title,
            "agent": agent,
            "risk": risk,
            "is_read_only": is_read_only,
            "blast_radius": blast_radius,
            "grant_agent": grant_agent,
            "reach": redact_field(reach),
            "deny_effect": deny_effect,
            "trigger": trigger,
            "trigger_name": named,
            # Where it came from, in the words every surface shows it by: the dashboard's cards,
            # its Inbox row, and the tag a channel's prompt carries.
            "source_label": approval_source_label(
                source=source,
                session=session,
                trigger=trigger,
                trigger_name=named,
                title=title,
                approval_id=approval_id,
            ),
            "asked_by": asked_by,
            "ts": time.time(),
        }

    async def _hold_approval(self, entry: dict[str, Any]) -> None:
        """Put a pending approval on every surface at once — the one registration there is.

        The registry row is what ``GET /api/approvals`` serves (Home's count and To triage, the
        phone queue, the workflow run view); the WS frame is what an open chat card and the
        out-of-context nudge render; the push wakes a phone; the Inbox row is the durable
        listing. They are written together so no surface can learn of an approval another does
        not list — the defect this replaced was a chat approval that reached only its own chat.
        """
        self._pending_approvals[entry["id"]] = entry
        self.broadcast_ws("approval", entry)
        self._push_approval(entry["id"])
        self._raise_approval_row(entry)
        # `ApprovalRequest` (AUTO crit 5): declared, selectable in the hook UI. Emitted alongside
        # the WS broadcast — the same moment the user is asked — so a hook can mirror the prompt
        # to another channel while the future is still pending.
        #
        # OBSERVATIONAL ONLY: the hook's result is not awaited into the decision and cannot
        # resolve the future. Letting a hook answer would turn a local approval gate into an
        # unreviewed remote one. The redacted tool name is passed, not the raw tool or its input.
        from personalclaw.triggers.lifecycle_fire import approval_request_payload
        from personalclaw.triggers.lifecycle_fire import fire as _fire_lifecycle

        await _fire_lifecycle(
            approval_request_payload(
                tool=entry["tool"],
                source=entry["source"],
                session_key=entry["session"],
                approval_id=entry["id"],
            ),
            tool_name=entry["tool"],
        )

    def _raise_approval_row(self, entry: dict[str, Any]) -> None:
        """The pending approval's Inbox row, raised through the attention seam.

        `emit_attention_item` rather than a store write, so the row lands in the LIVE store the
        Inbox serves and carries its one notification. Raised the moment the approval is, not
        after a grace period: the Inbox is a surface that reads this registry, and a surface
        that lags it is one a user can look at while the agent is waiting and see nothing — the
        measured defect. `refs.approval` is the registry id, which is how every reader that also
        lists the approval (To triage, Home's count, Mission Control) recognises the row as the
        same item rather than a second one.

        Best-effort: the approval is what the user is waiting on, and losing the decision to a
        bookkeeping failure is strictly worse than losing the row.
        """
        try:
            from personalclaw.inbox import ItemKind, emit_attention_item

            refs = {"approval": str(entry["id"]), "source_label": str(entry["source_label"])}
            if entry.get("session"):
                refs["session"] = str(entry["session"])
            emit_attention_item(
                self,
                source="system",
                kind="agent_request",
                item_kind=ItemKind.AGENT_REQUEST.value,
                title=f"{_ASK_TITLE}{entry.get('tool') or 'a tool'}",
                body=_approval_row_body(entry),
                refs=refs,
                dedup_key=f"approval:{entry['id']}",
            )
        except Exception:
            self._log.debug("approval inbox row failed", exc_info=True)

    def withdraw_approval(
        self,
        approval_id: str,
        *,
        outcome: str,
        request_id: str = "",
        session: str = "",
        ended: str = "",
    ) -> None:
        """Take an ended approval off every surface at once, saying HOW it ended.

        The registry row, the Inbox row (closed through the seam's own resolver on the live
        store) and every open card and list — the ``approval_resolved`` frame is the one signal
        they all act on. Every path that ends an approval comes through here, so none of them
        can leave a surface still asking.

        An answered one's Inbox row is HANDLED. One that ended unanswered (``expired``,
        ``cancelled``) is EXPIRED, and *ended* — required then — says why ("the loop that started
        its run was stopped"), on the row and its frame: "Handled" claimed a decision nobody made.

        The frame names the SESSION as well as the id: an open chat drops a frame for another
        session, and matches its card by the ``request_id`` it has always used. ``outcome`` is
        one of :data:`APPROVAL_OUTCOMES` and required, so no path can end an approval without
        stating which of the four things happened; ``approved`` stays on the frame for the
        readers that only need to know whether the call runs.

        An ANSWERED one also settles any "Denied, no answer" note for the same call asked in the
        same place (``auto_denials.settle_retried``): that call is decided now, so the note has
        nothing left to ask. Here, because this is where every door's answer arrives.
        """
        if outcome not in APPROVAL_OUTCOMES:
            raise ValueError(f"unknown approval outcome {outcome!r}")
        if outcome in UNANSWERED_OUTCOMES and not ended:
            raise ValueError(f"an approval that ended {outcome} says why it ended")
        entry = self._pending_approvals.pop(approval_id, None) or {}
        if entry:
            self._record_ending(approval_id, outcome, ended)
        self.__dict__.get("_channel_asked", set()).discard(approval_id)
        self.__dict__.get("_answered_alone", set()).discard(approval_id)
        # A prompt still open on the owner's channel is closed with how it ended — one of the four
        # outcomes, not "rejected" for all but one — so the message there says what happened
        # instead of offering buttons that answer nothing (`ChannelDelivery.request_approval`).
        pending = self.__dict__.get("_channel_prompts", {}).pop(approval_id, None)
        future = getattr(pending, "future", None)
        if future is not None and not future.done():
            future.set_result(outcome)
        try:
            from personalclaw.inbox import expire_attention_items, resolve_attention_items

            def settled(message: str) -> str:
                return settled_row_text(message, outcome, ended)

            if outcome in UNANSWERED_OUTCOMES:
                expire_attention_items(self, {"approval": approval_id}, ended=ended, retext=settled)
            else:
                resolve_attention_items(self, {"approval": approval_id}, retext=settled)
        except Exception:
            self._log.debug("could not close the inbox row for %s", approval_id, exc_info=True)
        if entry and outcome not in UNANSWERED_OUTCOMES:
            from personalclaw import auto_denials
            from personalclaw.approval_grants import YOU

            auto_denials.settle_retried(self, entry, answer=outcome, by=YOU)
        try:
            self.broadcast_ws(
                "approval_resolved",
                {
                    "id": approval_id,
                    "request_id": str(entry.get("request_id") or request_id or approval_id),
                    "session": str(entry.get("session") or session),
                    "approved": outcome == "approved",
                    "outcome": outcome,
                    **({"ended": ended} if outcome in UNANSWERED_OUTCOMES else {}),
                },
            )
        except Exception:
            self._log.warning("WS broadcast failed for approval resolution", exc_info=True)

    #: How many ended approvals :meth:`ended_as` remembers. It is read by the waiter the moment
    #: its wait returns, so it only has to outlive that hop; the bound keeps a long-lived gateway's
    #: record from growing with every approval it ever asked.
    _ENDINGS_KEPT = 512

    def _record_ending(self, approval_id: str, outcome: str, ended: str = "") -> None:
        endings: dict[str, tuple[str, str]] = self.__dict__.setdefault("_endings", {})
        endings.pop(approval_id, None)
        endings[approval_id] = (outcome, ended)
        while len(endings) > self._ENDINGS_KEPT:
            endings.pop(next(iter(endings)))

    def ended_as(self, approval_id: str) -> str:
        """How an approval this registry held ended — one of :data:`APPROVAL_OUTCOMES` — or ``""``.

        The waiter of :meth:`request_approval` gets a bool, which cannot tell "you denied it" from
        "nobody answered in time" from "the work stopped first". A relay that reports the decision
        to its caller (the subagent manager's audit row, ``approval_grants.ToolDecision``) reads it
        here.
        """
        return str(self.__dict__.get("_endings", {}).get(approval_id, ("", ""))[0])

    def why_ended(self, approval_id: str) -> str:
        """Why an approval this registry held ended with no answer, in the words its surfaces said
        it ("nobody answered within 2 hours", "the room that asked for it was archived"), or ``""``
        for one that was answered or never held. A relay that says where the asking work is why
        a call did not run (a room's transcript) reads it here, beside :meth:`ended_as`."""
        return str(self.__dict__.get("_endings", {}).get(approval_id, ("", ""))[1])

    def settle_granted(
        self, *, tool: str, tool_input: object = "", session: str = "", trigger: str = "", by: str
    ) -> int:
        """A call a GRANT approved without asking (`approval_grants`) settles its note too.

        A "Denied, no answer" note is handled once its call is asked again and answered
        (:meth:`withdraw_approval`). A retry the chat's Trust, YOLO, a remembered "Always allow" or
        any other standing grant approved was never asked, so it never reached the registry and the
        note stayed open over a call that had run. The grant is recorded as who decided
        (``refs.retry_by``). The call is described exactly as :meth:`_approval_entry` describes an
        asked one, so the two are compared on the same redacted strings.
        """
        from personalclaw import auto_denials

        entry = {
            "tool": redact_field(tool),
            "tool_input": redact_field(tool_input_to_str(tool_input)),
            "session": session,
            "trigger": trigger,
        }
        return auto_denials.settle_retried(self, entry, answer="approved", by=by)

    def end_approval(self, approval_id: str, *, outcome: str, window_secs: float = 0.0) -> None:
        """An approval its waiter stopped waiting for: ``expired`` or ``cancelled``.

        It failed closed, so the call does not run, and every surface says which of the two
        happened. A no-op once a decision has withdrawn it, which is what lets every waiter call
        this unconditionally on its way out.

        An ``expired`` one leaves a note in the Inbox (``auto_denials.note_expired``), because the
        approval's own row closes here: before, an approval nobody answered in time was gone from
        every surface, with nothing saying the call had been denied. ``window_secs`` is how long
        its waiter waited, which the note states. ``cancelled`` leaves none — the work that asked
        was stopped, and the SEL row below records why.
        """
        if outcome not in UNANSWERED_OUTCOMES:
            raise ValueError(f"{outcome!r} is an answer, not a way to end without one")
        entry = self._pending_approvals.get(approval_id)
        if entry is None:
            return
        from personalclaw import auto_denials

        window = window_secs or self.approval_window_secs()
        why = (
            self._why_cancelled(entry)
            if outcome == "cancelled"
            else f"nobody answered within {auto_denials.window_words(window)}"
        )
        self.withdraw_approval(approval_id, outcome=outcome, ended=why)
        if outcome == "cancelled":
            self._audit_cancelled(approval_id, why, entry=entry)
        else:
            auto_denials.note_expired(self, entry, who=_who_asked(entry), window_secs=window)

    def _why_cancelled(self, entry: dict[str, Any]) -> str:
        """Why an approval whose waiter was cancelled ended: the gateway stopping, when it is
        (every waiter is cancelled then, whatever its owner), else the owner's own record of how
        it ended when there is one, else the plain fact that its work was stopped."""
        from personalclaw import restart_request, shutdown_event
        from personalclaw.dashboard.approval_owner import UNVERIFIABLE, owner_ended

        if shutdown_event.is_set():
            verb = (
                "restarted"
                if restart_request.stopping_for() == restart_request.RESTARTING
                else "stopped"
            )
            return f"the gateway {verb} before anyone answered"
        reason = owner_ended(entry, subagents=self.subagents)
        if reason and reason != UNVERIFIABLE:
            return reason
        return "the work that asked for it was stopped"

    def end_session_approval(
        self, session: "_ChatSession", request_id: str, *, outcome: str, window_secs: float = 0.0
    ) -> None:
        """:meth:`end_approval` for a chat's approval — and the transcript row with it.

        The permission row is the chat's permanent record of the call; left unresolved, a reload
        rendered a live card whose buttons could no longer deliver anything. So an approval that
        ends unanswered records ``expired``/``cancelled`` there too, exactly when it leaves the
        registry and not otherwise: a decision already wrote its own verb.
        """
        approval_id = chat_approval_id(session.key, request_id)
        if approval_id in self._pending_approvals:
            _mark_permission_resolved(session.messages, request_id, outcome)
        self.end_approval(approval_id, outcome=outcome, window_secs=window_secs)

    def cancel_approval(self, approval_id: str, *, reason: str) -> bool:
        """End a pending approval whose owner has ended while its waiter still waits.

        The waiter is woken with a refusal — ``False`` for a background waiter, ``"cancelled"``
        for a chat's, which its runner refuses like any other non-approval — so the call never
        runs; the chat's transcript row records ``cancelled``; every surface drops the approval
        as ``cancelled``; and the SEL records why. Returns False when nothing was pending.
        """
        entry = self._pending_approvals.get(approval_id)
        if entry is None:
            return False
        fut = self._approval_futures.get(approval_id)
        if fut is not None and not fut.done():
            fut.set_result(False)
        request_id = str(entry.get("request_id") or "")
        session = self._sessions.get(str(entry.get("session") or ""))
        if session is not None and approval_id == chat_approval_id(session.key, request_id):
            held = session._approval_futures.get(request_id)
            if held is not None and not held.done():
                held.set_result("cancelled")
            _mark_permission_resolved(session.messages, request_id, "cancelled")
        self.withdraw_approval(approval_id, outcome="cancelled", ended=reason)
        self._audit_cancelled(approval_id, reason, entry=entry)
        return True

    def cancel_turn_approvals(self, session_key: str) -> int:
        """A turn is being stopped: every approval it is waiting on is over. Returns the count.

        Called by ``SessionManager.stop_turn`` BEFORE it cancels the provider, so it reaches
        every stop path there is (the stop button, a cancel-and-replace follow-up, the plan
        runner, a loop's stop) rather than whichever of them remembered. Without it a stopped
        turn parked on an approval stayed parked: the provider's cancel is acknowledged while
        the runner is still awaiting the future, so the approval stayed on Home, To triage and
        the Inbox for its full two-hour window, and answering it resumed the stopped turn.
        """
        name = session_key.removeprefix(DASHBOARD_SESSION_PREFIX)
        session = self._sessions.get(name)
        if session is None:
            return 0
        pending = [rid for rid, fut in session._approval_futures.items() if not fut.done()]
        return sum(
            self.cancel_approval(chat_approval_id(session.key, rid), reason="its turn was stopped")
            for rid in pending
        )

    def cancel_approvals(self, *, session_prefix: str, reason: str) -> int:
        """End every pending approval raised under a session key starting with *session_prefix*.

        For an owner that has ended and knows only its own key space — a workflow run's stages
        are ``workflow:<run>:<node>`` — so its end reaches every approval it raised without the
        owner having to know how each waiter was wired. Returns the count.
        """
        if not session_prefix:
            return 0
        owned = [
            aid
            for aid, entry in self._pending_approvals.items()
            if str(entry.get("session") or "").startswith(session_prefix)
        ]
        return sum(self.cancel_approval(aid, reason=reason) for aid in owned)

    def asks_under(self, session_prefix: str) -> list[dict[str, Any]]:
        """The pending approvals raised under a session key starting with *session_prefix*, as
        every surface lists them: what a workflow run's steps (``workflow:<run>:``) wait on their
        owner's answer for, which a status read of the run says (`workflows.service.status`)."""
        if not session_prefix:
            return []
        return [
            dict(entry)
            for entry in self._pending_approvals.values()
            if str(entry.get("session") or "").startswith(session_prefix)
        ]

    def refuse_ended_owner(self, approval_id: str) -> str:
        """If the work that asked for this approval has ended, cancel it and return why; else "".

        The decision path's defence in depth. Cancelling an owner already ends its approvals at
        the source; this is the second line, asked before ANY door delivers an answer (the chat
        card, ``POST /api/approvals/{id}/{action}``, the bulk trust/YOLO switch, a channel's
        reply), so a path that forgot to end one cannot turn an Approve into work for something
        that is already over — the measured case being a cancelled run's stage that spawned its
        subagent when the approval it left behind was approved from Home.
        """
        entry = self._pending_approvals.get(approval_id)
        if entry is None:
            return ""
        from personalclaw.dashboard.approval_owner import owner_ended

        reason = owner_ended(entry, subagents=self.subagents)
        if reason:
            self.cancel_approval(approval_id, reason=reason)
        return reason

    def _audit_cancelled(
        self, approval_id: str, reason: str, *, entry: dict[str, Any] | None = None
    ) -> None:
        """One SEL row per approval that ended because its owner did — a state change of a
        security control, and the only record that a pending request was withdrawn unanswered."""
        try:
            owner = (entry or {}).get("session") or (entry or {}).get("source") or "unknown"
            sel().log_api_access(
                caller=f"approval_owner:{owner}",
                operation="approval_cancelled",
                outcome="cancelled",
                resources=f"{approval_id}: {reason}"[:300],
            )
        except Exception:
            self._log.debug("SEL audit failed for a cancelled approval", exc_info=True)

    def close_orphaned_approval_rows(self) -> int:
        """Close every open Inbox row whose approval is no longer pending. Returns the count.

        No approval survives a restart — the futures are in memory and the turns that awaited
        them are gone — so after one, a row still asking for an approval is asking for nothing:
        opening it finds a card whose buttons can no longer deliver an answer. Nobody answered
        it, so it expires saying the restart ended it. Run when the gateway attaches its Inbox,
        and safe at any other time: an approval still in the registry keeps its row.
        """
        from personalclaw.inbox import (
            GATEWAY_RESTARTED,
            OPEN_STATUSES,
            expire_attention_items,
            live_store,
        )

        store = live_store(self)
        if store is None:
            return 0
        orphaned = {
            str(item.refs.get("approval"))
            for item in store.items.values()
            if item.status in OPEN_STATUSES
            and item.refs.get("approval")
            and str(item.refs.get("approval")) not in self._pending_approvals
        }
        return sum(
            expire_attention_items(
                self, {"approval": aid}, ended=GATEWAY_RESTARTED, retext=_restarted_text
            )
            for aid in sorted(orphaned)
        )

    def settle_verification_rows(self) -> int:
        """Settle what the second opinion left on the previous run's Inbox. Returns the count.

        A check the restart cut off is delivered, and a decision row an earlier verify filed as
        ``filtered`` leaves Filtered: restored if the decision still stands, handled if not
        (``inbox.settle_verification_rows``). Run when the gateway attaches its Inbox, before
        :meth:`close_orphaned_approval_rows`.
        """
        from personalclaw.inbox import live_store, settle_verification_rows

        store = live_store(self)
        if store is None:
            return 0
        return len(
            settle_verification_rows(
                self, store, decision_pending=self._decision_stands, expired_text=_restarted_text
            )
        )

    def _decision_stands(self, row: Any) -> bool:
        """Whether the decision a filtered row asks for is still open.

        Three emitters raise decision rows on `system/agent_request`. An approval row stands
        while its approval is in the registry, which no restart survives. A project-trust prompt
        stands until its folder is trusted. A one-tap hold is its own record, so it stands.
        """
        approval = row.refs.get("approval")
        if approval:
            return str(approval) in self._pending_approvals
        if row.refs.get("guardrail") == "project_trust":
            from personalclaw.guardrails.project_trust import DECISION_TRUSTED, project_decision

            return project_decision(str(row.refs.get("dir") or "")) != DECISION_TRUSTED
        return True

    def _push_approval(self, approval_id: str) -> None:
        """Wake the phone for a pending approval — MOBILE-COMPANION `MC-5`'s milestone.

        **Only the approval id travels.** Not the tool, not its arguments, not the session.
        The phone opens ``#/companion?approval=<id>`` and re-fetches the card from
        ``GET /api/approvals`` over the user's own link, so the decision's content never
        enters a push service. This is the whole reason the payload contract is asserted in
        :mod:`personalclaw.push` rather than left to each caller.

        Routed through plan 42's rules rather than pushed unconditionally: the user owns
        "does a blocked run reach my phone", and ``approval/requested`` is a real row in the
        rules matrix (it ships with ``push`` among its default targets). A ``never`` mode or
        a targets list without ``push`` silences this and nothing else.

        NOT routed through :meth:`notify`: that would add a desktop toast beside the approval
        card the dashboard already renders — a behaviour change to every existing user, in
        exchange for nothing the phone needs.

        A chat channel asks too (:meth:`_ask_on_a_channel`), with Approve/Deny where it has them:
        the channel the chat started on, whatever the rule says, and the rule's ``channel_dm``
        target adds the owner's "Send approvals to" (:meth:`_asking_channels`).
        """
        try:
            from personalclaw import notification_kinds, notification_rules, push

            registered = notification_kinds.kind_for_legacy(notification_kinds.APPROVAL)
            rule = notification_rules.resolve_rule(registered.source, registered.kind)
            pings = rule.mode != "never"
            if pings and "push" in rule.targets:
                push.deliver_async("approval", approval_id)
            asking = self._asking_channels(approval_id, pings and "channel_dm" in rule.targets)
            self._ask_on_a_channel(approval_id, asking)
        except Exception:
            self._log.debug("approval push dispatch failed", exc_info=True)

    def _asking_channels(self, approval_id: str, channel_dm: bool) -> list[str]:
        """The chat channels that may ask *approval_id*, in the order they are tried.

        The channel the chat started on (``channel_provider_for``) asks whatever the Approval
        needed row says. It is where the person asking is, so its prompt is that chat's approval
        card, which PersonalClaw shows for a chat of its own whatever the rule says too; waiting
        for a ``channel_dm`` target there left a chat started on Telegram asking nobody on
        Telegram. The rule's ``channel_dm`` target (*channel_dm*, which ``never`` turns off)
        adds the rest of ``channel_delivery.approval_providers``: "Send approvals to", for an
        approval with no channel origin and after an origin that cannot ask. Without it no other
        channel stands in, and the approval waits in PersonalClaw, where every approval is listed.
        """
        from personalclaw import channel_delivery

        entry = self._pending_approvals.get(approval_id) or {}
        session = str(entry.get("session") or "")
        origin = self.channel_provider_for(session) if session else ""
        if not channel_dm:
            return [origin] if origin and channel_delivery.delivery_for(origin) is not None else []
        providers = channel_delivery.approval_providers(origin)
        chosen = channel_delivery.approval_channel() if not providers else ""
        if chosen:
            self._log.info(
                "approval %s: approvals go to %s, which is not connected, so it waits in "
                "PersonalClaw",
                approval_id,
                chosen,
            )
        return providers

    def _ask_on_a_channel(self, approval_id: str, providers: list[str]) -> None:
        """Ask the owner on the first of *providers* that can (:meth:`_approval_on_a_channel`).

        Skipped when the caller is already asking there (``asked_on_channel``), and when no channel
        may ask. Runs as a task on the gateway's loop, so the approval is listed everywhere else
        first and never waits on a channel."""
        entry = self._pending_approvals.get(approval_id)
        if not entry or not providers or approval_id in self.__dict__.get("_channel_asked", set()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._log.debug("approval %s: no loop here to ask on a channel from", approval_id)
            return
        task = loop.create_task(self._approval_on_a_channel(approval_id, dict(entry), providers))
        tasks = getattr(self, "_background_tasks", None)
        if isinstance(tasks, set):
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    async def _approval_on_a_channel(
        self, approval_id: str, entry: dict[str, Any], providers: list[str]
    ) -> None:
        """Ask on the first of *providers* that can: its answers where it has buttons, else a link.

        *providers* is :meth:`_asking_channels`: the channel the chat started on first, asked in
        that chat, since the person asking is there; then, with the ``channel_dm`` target, the
        owner's "Send approvals to" channel alone when they chose one, else every connected
        channel in name order, as ``channel_delivery.reach_owner`` tries them. The first one with
        an owner id and a ``request_approval`` prompt asks, offering what :meth:`channel_answers`
        offers there, and a press there answers this approval the way the dashboard's buttons do
        (:meth:`answer_on_channel`). An answer given anywhere else, and the approval expiring or
        being cancelled, close that prompt with how it ended (:meth:`withdraw_approval`). When
        none of them can prompt, the owner gets a message with the link to answer it instead, on
        the same channels.

        The channel is handed a short token, not the approval id: a chat's id carries its session
        key, and a button's data has a size cap on some channels. It is handed the entry's brief
        too (``approval_brief.entry_approval_brief``), which is what its prompt shows: the tool,
        its arguments and purpose as this entry holds them for the dashboard's card, what the
        call can touch, and the answers it offers."""
        import secrets
        from types import SimpleNamespace

        from personalclaw import channel_delivery
        from personalclaw.approval_brief import APPROVAL_BRIEF_META_KEY, entry_approval_brief
        from personalclaw.config.credentials import owner_id_for

        prompts: dict[str, Any] = self.__dict__.setdefault("_channel_prompts", {})
        session = str(entry.get("session") or "")
        origin = self.channel_provider_for(session) if session else ""
        token = secrets.token_hex(6)
        for provider in providers:
            delivery = channel_delivery.delivery_for(provider)
            ask = getattr(delivery, "request_approval", None)
            if delivery is None or ask is None or not owner_id_for(provider):
                continue
            answers = self.channel_answers(entry, in_its_chat=provider == origin)
            brief = entry_approval_brief(entry, answers=answers)
            event = SimpleNamespace(
                request_id=token,
                title=str(entry.get("tool") or ""),
                tool_input=str(entry.get("tool_input") or ""),
                tool_purpose=str(entry.get("tool_purpose") or ""),
                risk_level=str(entry.get("risk") or ""),
                tool_meta={APPROVAL_BRIEF_META_KEY: brief} if brief else {},
            )
            seen: dict[str, Any] = {}

            def _on_prompted(pending: Any, _seen: dict[str, Any] = seen) -> None:
                _seen["pending"] = pending
                prompts[approval_id] = pending

            # The origin channel asks in the chat the turn came from (its linked thread), the
            # way the gateway's own approvals do; any other channel asks in the owner's DM.
            where: dict[str, Any] = (
                {"parent_session_key": f"dashboard:{session}", "sessions": self.sessions}
                if provider == origin
                else {}
            )
            try:
                approved = await ask(
                    event,
                    # Where it came from, as every surface names it: a loop's worker asks on
                    # the chat path, and its prompt still says the loop.
                    source=str(entry["source_label"]),
                    on_prompted=_on_prompted,
                    **where,
                )
            except Exception:  # noqa: BLE001 - one channel failing hands over to the next
                self._log.warning(
                    "channel %s: asking for an approval failed", provider, exc_info=True
                )
                continue
            finally:
                if prompts.get(approval_id) is seen.get("pending"):
                    prompts.pop(approval_id, None)
            if approved is None and "pending" not in seen:
                continue  # this channel could not prompt the owner; the next one may
            future = getattr(seen.get("pending"), "future", None)
            if (
                future is not None
                and future.done()
                and not future.cancelled()
                and approval_id in self._pending_approvals
            ):
                # The channel's app checked the press is its paired owner's (the contract of
                # `ChannelDelivery.request_approval`), so this is you, on that channel. The
                # future holds the answer pressed, which says more than the yes/no returned.
                self.answer_on_channel(
                    approval_id,
                    str(future.result()),
                    offered=answers,
                    by=approval_answer.on_channel(provider),
                )
            return
        await self._approval_link_on_a_channel(approval_id, entry, providers)

    def channel_answers(
        self, entry: dict[str, Any], *, in_its_chat: bool
    ) -> tuple["ApprovalAnswer", ...]:
        """What a channel's prompt for *entry* offers: the dashboard card's answers, for that call.

        Allow once and Deny always. "Allow for this chat" too, which is the card's "This chat"
        (the chat's Trust), when all of these hold, and otherwise the prompt answers this call
        alone:

        * the approval is a chat's own, and the prompt is asked in that chat (*in_its_chat*: the
          channel the chat started on, asking in it). Anywhere else "this chat" would name the
          conversation the prompt is in rather than the one asking, and the surfaces outside a
          chat (Home, the Inbox, the phone) offer Approve and Deny alone;
        * the call may not destroy anything (``task_modes.MAY_DESTROY``): the card withholds its
          standing answers on such a call until the owner unlocks them, and a prompt has no
          unlock, so it offers what the card offers before one;
        * the call reaches no host off the allowed hosts (the entry's ``reach``): such a call is
          asked about whatever a grant says (``run_bounds``), so the card offers no standing
          answer for it, and "Allow for this chat" would promise a "without asking" it never gets;
        * the operator ceiling lets a chat's Trust stand, as the card's own route asks before it
          grants it (``approval_grants.stands``): an answer that would be refused is not offered.

        The card's "This agent" is not offered: it saves a setting on the agent that outlives the
        chat, and the card says per agent whether it can, which is the card's to show.
        """
        from personalclaw import approval_grants
        from personalclaw.channel_delivery import (
            ALLOW_FOR_THIS_CHAT,
            ALLOW_ONCE,
            DENY,
            ONE_CALL_ANSWERS,
        )
        from personalclaw.task_modes import MAY_DESTROY

        session = self._sessions.get(str(entry.get("session") or ""))
        its_own = session is not None and entry.get("id") == chat_approval_id(
            session.key, str(entry.get("request_id") or "")
        )
        if (
            not in_its_chat
            or not its_own
            or str(entry.get("risk") or "") in MAY_DESTROY
            or entry.get("reach")
            or not approval_grants.stands(approval_grants.TRUST, caller="channel", audit=False)
        ):
            return ONE_CALL_ANSWERS
        return (ALLOW_ONCE, ALLOW_FOR_THIS_CHAT, DENY)

    def answer_on_channel(
        self,
        approval_id: str,
        answer: str,
        *,
        offered: tuple["ApprovalAnswer", ...],
        by: Principal,
    ) -> bool:
        """Answer a pending approval with what the owner pressed on a channel's prompt.

        *answer* is the ``key`` the prompt's future was resolved with, and only one of the
        answers that prompt *offered* answers anything: any other value decides nothing and is
        logged, so the approval keeps waiting where else it is listed. Allow once and Deny answer
        it as the dashboard's Approve and Deny do (:meth:`resolve_approval`). "Allow for this chat"
        answers it as the card's "This chat" does, through the same decision path
        (:meth:`decide_session_approval` with ``trust``): the chat is trusted, its next calls run
        without asking, its header shows it and the owner can turn it off there. Held to the
        operator ceiling at the moment it is given, as the card's route holds it. Returns whether
        the approval was answered.
        """
        from personalclaw import approval_grants
        from personalclaw.channel_delivery import ALLOW_FOR_THIS_CHAT

        chosen = next((a for a in offered if a.key == answer), None)
        if chosen is None:
            self._log.warning(
                "approval %s: a channel answered %r, which its prompt did not offer; nothing "
                "was decided",
                approval_id,
                answer[:40],
            )
            return False
        if chosen != ALLOW_FOR_THIS_CHAT:
            return self.resolve_approval(approval_id, chosen.ends == "approved", by=by)
        if self.answer_refusal(approval_id, by) or self.refuse_ended_owner(approval_id):
            return False
        entry = self._pending_approvals.get(approval_id) or {}
        session = self._sessions.get(str(entry.get("session") or ""))
        request_id = str(entry.get("request_id") or "")
        if session is None:
            return False
        held = session._approval_futures.get(request_id)
        if held is None or held.done():
            return False
        if not approval_grants.stands(
            approval_grants.TRUST, caller=by.label, subject=f"scope={chosen.key}"
        ):
            return False
        self.decide_session_approval(session, request_id, chosen.key, by=by)
        return True

    async def _approval_link_on_a_channel(
        self, approval_id: str, entry: dict[str, Any], providers: list[str]
    ) -> None:
        """Tell the owner on their channel that an approval is waiting, with where to answer it.

        Tried on the channels that could not prompt, in the same order (:meth:`_asking_channels`):
        the chat's own channel first, then "Send approvals to" — so the link never lands on a
        channel the owner did not choose, and a chat that started on a channel hears about its
        approval there."""
        from personalclaw.channel_delivery import reach_owner
        from personalclaw.dashboard.channel_messages import dashboard_link

        what = str(entry.get("tool") or "a tool call")
        why = str(entry.get("tool_purpose") or "")
        link = dashboard_link(f"#/companion?approval={approval_id}")
        text = f"PersonalClaw is waiting for your approval: {what}, from {entry['source_label']}"
        text += f", to {why}" if why else ""
        text += f". Answer it here: {link}" if link else ". Answer it in PersonalClaw."
        outcome = None
        for provider in providers:
            outcome = await reach_owner(
                lambda delivery, dm: delivery.deliver_text(dm, text), only=provider
            )
            if outcome.delivered:
                return
        if outcome is not None and not outcome.no_channel:
            self._log.warning(
                "approval %s: no channel reached the owner: %s", approval_id, outcome.sentence()
            )

    def answer_refusal(self, approval_id: str, by: Principal) -> str:
        """Why *by* may not answer the pending approval *approval_id*, audited; ``""`` if it may.

        ``approval_answer``'s rule for this registry: only you answer, and never the principal the
        approval recorded as asking it (``asked_by``). Asked by every door before it delivers an
        answer, and again by :meth:`resolve_approval` and :meth:`decide_session_approval`
        themselves, so a door that forgot to ask still answers nothing.
        """
        entry = self._pending_approvals.get(approval_id) or {}
        return approval_answer.check(
            by, what=f"approval:{approval_id}", asked_by=str(entry.get("asked_by") or "")
        )

    def resolve_approval(self, approval_id: str, approved: bool, *, by: Principal) -> bool:
        """Answer a pending approval by its REGISTRY id, from any surface. False if not pending.

        *by* is who is answering. Only you answer, and never the party that asked
        (:meth:`answer_refusal`): anyone else is refused and audited, and this returns False.

        A background origin's future lives here and receives the ``bool`` its gateway waiter
        converts. A chat-held approval is answered by :meth:`decide_session_approval` — the very
        path the chat's own card takes — so a decision made on Home, the phone or the Inbox
        writes the same record and the same audit row, and meets the same refusal handling in
        the waiting runner. There is no second way to answer a chat's approval.

        An approval whose owner has ended is not answered at all: :meth:`refuse_ended_owner`
        cancels it and this returns False, so no door can deliver an Approve to work that is over.
        """
        if self.answer_refusal(approval_id, by):
            return False
        if self.refuse_ended_owner(approval_id):
            return False
        fut = self._approval_futures.get(approval_id)
        if fut and not fut.done():
            fut.set_result(approved)
            self.withdraw_approval(approval_id, outcome="approved" if approved else "rejected")
            try:
                sel().log_tool_invocation(
                    session_key="state",
                    tool_name="approval_decision",
                    outcome="approved" if approved else "rejected",
                    request_id=approval_id,
                    # Who answered: you, or you on a named channel.
                    source=by.label,
                )
            except Exception:
                self._log.warning("SEL audit failed for approval resolution", exc_info=True)
            return True
        entry = self._pending_approvals.get(approval_id)
        session = self._sessions.get(str(entry.get("session") or "")) if entry else None
        if session is None:
            return False
        request_id = str(entry.get("request_id") or "") if entry else ""
        held = session._approval_futures.get(request_id)
        if held is None or held.done():
            return False
        self.decide_session_approval(
            session, request_id, "approved" if approved else "rejected", by=by
        )
        return True

    def decide_session_approval(
        self, session: "_ChatSession", request_id: str, action: str, *, by: Principal
    ) -> dict[str, object] | None:
        """Answer the approval a chat is waiting on — the ONE decision path, whichever door.

        The chat's card (``POST /api/chat/sessions/{key}/approve``) and every surface outside
        the chat (``POST /api/approvals/{id}/{action}``: Home's To triage, the phone companion)
        arrive here. So a decision made anywhere persists the same transcript record, writes the
        same SEL row, withdraws the approval from every surface, and wakes the same waiting
        runner — whose refusal handling (the rest of a refused batch is refused, not re-asked,
        so the agent cannot route around a Deny with a second call) is therefore the same for
        every door.

        The caller has established that *request_id* names a pending future on *session* and
        that *action* is in :data:`SESSION_APPROVAL_ACTIONS`. Returns what a ``trust_agent``
        grant actually did, or None for every other verb: a scope that grants nothing has no
        grant to describe, and an always-present object with ``persisted: false`` would read as
        a failed grant on an Allow-once (#541/#683).

        *by* is who is answering, held to :meth:`answer_refusal` here as well as at the door: a
        refused answer raises :class:`AnswerRefused` and decides nothing, whatever the door did.
        """
        refused = self.answer_refusal(chat_approval_id(session.key, request_id), by)
        if refused:
            raise AnswerRefused(refused)
        name = session.key
        original_action = action
        grant: dict[str, object] | None = None
        # Trust: auto-approve remaining tools for this session
        if action == "trust":
            session._trust = True
            session._trust_from_floor = ""  # yours now, not a floor's to withdraw
            self.sessions.set_approval_policy(f"dashboard:{name}", "auto")
            action = "approved"
        # Trust-agent ("Always allow for this agent"): trust THIS chat now (like trust)
        # AND persist the grant onto the bound agent's profile (approval_mode="auto") so
        # every future chat with that agent starts auto-approving — seeded at session-open
        # by chat_runner. One vocabulary, one gate: this just writes the persistent floor
        # the runtime already consumes. Skipped for the default/unnamed agent (no editable
        # profile) and reserved system agents (their config is fixed).
        elif action == "trust_agent":
            session._trust = True
            session._trust_from_floor = ""
            self.sessions.set_approval_policy(f"dashboard:{name}", "auto")
            action = "approved"
            from personalclaw.agents.defaults import persistable_grant_target

            # The grant target, resolved by the ONE owner that also feeds the card's promise at
            # prompt time (chat_runner's perm_meta["grant_agent"]). Deciding it here a second
            # way is what let the card and the write path disagree: the card said "in this chat
            # and future ones" while this branch's `else` degraded the grant to session scope
            # and told only the log. Now the outcome is a value, so it can be REPORTED — on the
            # wire, in the transcript row, and in the SEL.
            agent_name = ""
            try:
                cfg = config_loader.AppConfig.load()
                target = persistable_grant_target(session.agent or "", cfg)
                if target:
                    prof = cfg.agents[target]
                    if prof.approval_mode != "auto":
                        prof.approval_mode = "auto"
                        cfg.save()
                    # Set only AFTER the write returned. A failed save is not a persisted
                    # grant, and the report below is read as a statement about the file.
                    agent_name = target
            except Exception:
                self._log.warning("Failed to persist always-for-agent grant", exc_info=True)
            grant = {"scope": "agent", "persisted": bool(agent_name), "agent": agent_name}
            try:
                # Best-effort, and OUTSIDE the block above: an audit that raises must not turn a
                # grant that persisted into one this path reports as session-scope. Both
                # outcomes get a row — "the user asked for a standing grant and did not get one"
                # is exactly the event an auditor reconstructing a later ask would look for.
                sel().log_api_access(
                    caller="dashboard:approval",
                    operation="mode_change:always_for_agent",
                    outcome="enabled" if agent_name else "session_scope_only",
                    resources=f"{name} agent={agent_name or (session.agent or '').strip() or '(default)'}",  # noqa: E501
                )
            except Exception:
                self._log.warning("SEL audit failed for always-for-agent grant", exc_info=True)
        # Trust-reads: auto-approve read-only bash commands for this session
        # Defer setting _trust_reads until after the approval future is consumed
        # to prevent the frontend from seeing trust_reads=true while still pending.
        elif action == "trust_reads":
            action = "approved_trust_reads"
        # YOLO: auto-approve all tools globally (all sessions)
        elif action == "yolo":
            self.enable_yolo()
            for s in self._sessions.values():
                self.sessions.set_approval_policy(f"dashboard:{s.key}", "auto")
            action = "approved"
        # A loop's worker asks for its loop. A standing grant given on its card reaches every
        # worker of this run of the loop — the stage worker, its task workers, and the ones the
        # scheduler starts later — rather than the one session that happened to ask
        # (`loop.manager.grant_every_worker`). An agent-wide grant does too: the other workers
        # would otherwise not read the agent's new floor until they are armed again.
        if original_action in ("trust", "trust_agent"):
            from personalclaw.loop import manager as loop_manager
            from personalclaw.loop.plan_walkthrough import planner_loop_id

            # A planner's card grants the same: its later passes, and the loop's workers until
            # the run ends — a launch is a run beginning, which asks again.
            loop_id, _task_id = loop_manager.worker_ids(name)
            if not loop_id:
                loop_id = planner_loop_id(name)
            if loop_id:
                loop_manager.grant_every_worker(self, loop_id)
        resolved = action if action in ("approved", "approved_trust_reads") else "rejected"
        session._approval_futures[request_id].set_result(resolved)
        # Persist resolved state into the permission message so it survives tab switches.
        #
        # The RECORD is not the future's value (#683). `trust_agent` is remapped to "approved"
        # above because that is what the awaiting tool call must see, and the record used to
        # inherit that remap — so a standing per-agent grant and a one-off Allow left
        # byte-identical transcript rows, while their side effects differ by an auto-approval
        # policy that explains every later silent run. The transcript is the permanent record of
        # a security decision, so it keeps the verb the user chose.
        #
        # THREE outcomes, not two, because the grant has three (#541 + #683): allow-once,
        # granted-and-persisted, and granted-but-session-scope-only. Collapsing the last two
        # would re-lose exactly the fact #541 is about — whether "in this chat and future ones"
        # actually happened. `trust`/`trust_reads` were already preserved and are unchanged.
        if original_action == "trust_agent":
            record = "trust_agent" if (grant or {}).get("persisted") else "trust_agent_session"
        elif original_action in ("trust", "trust_reads"):
            record = original_action
        else:
            record = resolved
        _mark_permission_resolved(session.messages, request_id, record)
        # Withdraw first so every surface is unblocked before the bookkeeping below.
        self.withdraw_approval(
            chat_approval_id(name, request_id),
            outcome="rejected" if resolved == "rejected" else "approved",
            request_id=request_id,
            session=name,
        )
        self.push_sessions_update()
        # SEL audit (best-effort — must not block the UI-unblocking path above). It names who
        # answered (you, or you on a channel), since every door decides through here.
        try:
            sel().log_api_access(
                caller=f"dashboard:{name}",
                operation=f"tool_approval:{original_action}",
                outcome=resolved,
                resources=request_id,
                metadata={"decided_by": by.label},
            )
        except Exception:
            self._log.warning("SEL audit failed for approval %s", request_id, exc_info=True)
        return grant
