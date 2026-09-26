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

from personalclaw.config import loader as config_loader
from personalclaw.constants import DASHBOARD_SESSION_PREFIX
from personalclaw.security import redact_field
from personalclaw.sel import sel
from personalclaw.task_modes import read_only_command, tool_input_to_str

if TYPE_CHECKING:
    from personalclaw.dashboard.state import _ChatSession


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

#: Why an app may not answer an approval raised in a conversation the app itself started — the
#: refusal ``handlers/sessions.api_approval_resolve`` gives (the chat's own approve route is the
#: owner's outright, in ``apps/permissions.ROUTE_AUTHZ``). The relay a companion runs there
#: carries YOUR decision; in the app's own conversation the app answering would be the app
#: deciding its own request, past whatever made that conversation ask (the operator ceiling, or
#: an ``agent`` grant it no longer holds).
APP_OWN_APPROVAL_REFUSAL = (
    "an approval raised in a conversation this app started is yours to answer, not the app's"
)

#: How a pending approval ENDS — the ``outcome`` every ``approval_resolved`` frame carries. The
#: first two are a person's answer. The last two are an approval ending with NO answer:
#: ``expired`` — nobody answered inside its window; ``cancelled`` — the work that asked stopped
#: first (its chat turn, its subagent, its workflow run, its loop). Both fail closed, so the call
#: does not run, but neither is "denied": that word states a decision a person made, and the two
#: surfaces that used to say it for a stopped turn (the live card and, after a reload, a card
#: whose buttons could no longer deliver anything) were each telling the user something untrue.
APPROVAL_OUTCOMES = frozenset({"approved", "rejected", "expired", "cancelled"})
#: The two ways an approval ends without an answer. Also the two values a chat transcript's
#: permission row records for them, so a reload renders what happened instead of a live card.
UNANSWERED_OUTCOMES = frozenset({"expired", "cancelled"})


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


def _approval_row_body(entry: dict[str, Any]) -> str:
    """What a pending approval's Inbox row says. Server-composed product copy: every clause true.

    Names who is waiting (the chat's agent and the chat, or the background origin), on what, at
    what risk, and then what the call would actually do — the redacted arguments the listing
    carries — so the row can be judged from the Inbox rather than only opened.
    """
    tool = str(entry.get("tool") or "a tool")
    agent = str(entry.get("agent") or "")
    title = str(entry.get("session_title") or "")
    if agent:
        # Only a chat-held approval names its agent; that is how the two origins are told apart.
        who = f"{agent} in “{title}”" if title else f"{agent} in a chat"
    elif entry.get("source") == "subagent":
        who = f"A subagent of “{title}”" if title else "A subagent"
    else:
        who = "A background task"
    risk = str(entry.get("risk") or "")
    lines = [
        f"{who} is waiting for your decision on {tool}" + (f" (risk: {risk})." if risk else ".")
    ]
    for detail in (entry.get("tool_purpose"), entry.get("tool_input")):
        text = " ".join(str(detail or "").split())
        if text:
            lines.append(text if len(text) <= 200 else text[:199] + "…")
    return "\n".join(lines)


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

    _APPROVAL_TIMEOUT = 7200  # 2 hours — interactive default (a human is present)
    # Unattended origins (cron / loop / heartbeat / scheduled) have no human to
    # answer a prompt, so a long wait just hangs the run. They fail CLOSED to
    # deny after a short window. Keyed by a substring of the approval `source`.
    _UNATTENDED_APPROVAL_TIMEOUT = 300  # 5 minutes
    _UNATTENDED_SOURCE_MARKERS = ("cron", "loop", "heartbeat", "schedule", "autonudge")

    def _approval_timeout_for(self, source: str) -> float:
        """Resolve the response window for an approval by its origin.

        Unattended origins (no human at the keyboard) get a short window and fail
        closed to deny on expiry, so an autonomous run can't hang for hours on a
        prompt nobody will answer; interactive origins keep the long window.
        """
        low = (source or "").lower()
        if any(marker in low for marker in self._UNATTENDED_SOURCE_MARKERS):
            return self._UNATTENDED_APPROVAL_TIMEOUT
        return self._APPROVAL_TIMEOUT

    async def request_approval(
        self,
        approval_id: str,
        source: str,
        tool: str,
        *,
        tool_input: object = "",
        tool_purpose: str = "",
        session: str = "",
    ) -> bool:
        """Request interactive approval. Returns True if approved, False if rejected/timeout.

        The timeout is origin-aware (see :meth:`_approval_timeout_for`): unattended
        sources deny fast, interactive sources wait longer. Timeout always fails
        closed to deny.

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

        entry = self._approval_entry(
            approval_id,
            request_id=approval_id,
            source=source,
            tool=tool,
            tool_input=display_input,
            tool_purpose=tool_purpose,
            session=session,
            # #2821: the same command-screening verdict the chat card gets, from the same
            # owner, so the two surfaces that ask a human for permission cannot describe
            # one call differently.
            #
            # Screened on the RAW `tool_input`, NOT on `display_input`: the entry's copy has had
            # URLs and credentials rewritten, and screening a string the shell will never see
            # is how a verdict stops describing the actual call. The raw object is also the
            # more precise input — `read_only_command` is typed `object` precisely so it can
            # read a native dict's `command` key instead of re-parsing a serialized copy.
            # `None` when this is not a shell call.
            is_read_only=read_only_command(tool, "", tool_input),
        )
        timeout = self._approval_timeout_for(source)
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
            # Fail closed: an unanswered prompt denies. Audit unattended timeouts
            # so a silently-denied autonomous action is traceable.
            try:
                from personalclaw.sel import sel

                sel().log_api_access(
                    caller=f"approval_timeout:{source}",
                    operation="approval_timeout:denied",
                    outcome="denied",
                    resources=f"tool={entry['tool'][:80]} after={int(timeout)}s",
                )
            except Exception:
                self._log.debug("SEL audit failed for approval timeout", exc_info=True)
            return False
        finally:
            self._approval_futures.pop(approval_id, None)
            self.end_approval(approval_id, outcome="expired" if timed_out else "cancelled")

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
        is_read_only: bool | None,
        grant_agent: str,
    ) -> None:
        """Publish the approval a chat's runner is about to wait on, under
        :func:`chat_approval_id`.

        The caller has ALREADY parked its future on ``session._approval_futures[request_id]``:
        this publishes it, and the publication is what makes it answerable from outside the chat,
        so an answer that arrives the instant it is listed must find the future in place.

        ``tool_input`` is the caller's already-sanitized display string (the same one the
        transcript row persists), and ``is_read_only`` its verdict on the RAW input — the
        screening rule `request_approval` states, kept by the one caller that holds the raw
        object.
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
            agent=agent,
            risk=risk,
            grant_agent=grant_agent,
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
        is_read_only: bool | None,
        agent: str = "",
        risk: str = "",
        grant_agent: str = "",
    ) -> dict[str, Any]:
        """The ONE shape a pending approval has, whatever raised it.

        This dict is at once the ``GET /api/approvals`` row, the ``approval`` WS frame (the chat
        card, the out-of-context nudge, the phone queue) and the source of the Inbox row — so a
        field supplied here reaches every door, and no door can describe the call differently.

        Every LLM-sourced string is redacted here, once, for both origins. ``agent``/``risk``/
        ``grant_agent`` are known only to a chat and stay empty for a background origin: empty
        is "not known", never "none".
        """
        live = self._sessions.get(session) if session else None
        # A session's title defaults to its key until the chat is named; a key is not a title.
        title = live.title if live is not None and live.title and live.title != live.key else ""
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
            "session_title": redact_field(title),
            "agent": agent,
            "risk": risk,
            "is_read_only": is_read_only,
            "grant_agent": grant_agent,
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

            refs = {"approval": str(entry["id"])}
            if entry.get("session"):
                refs["session"] = str(entry["session"])
            emit_attention_item(
                self,
                source="system",
                kind="agent_request",
                item_kind=ItemKind.AGENT_REQUEST.value,
                title=f"Approval needed: {entry.get('tool') or 'a tool'}",
                body=_approval_row_body(entry),
                refs=refs,
                dedup_key=f"approval:{entry['id']}",
            )
        except Exception:
            self._log.debug("approval inbox row failed", exc_info=True)

    def withdraw_approval(
        self, approval_id: str, *, outcome: str, request_id: str = "", session: str = ""
    ) -> None:
        """Take an ended approval off every surface at once, saying HOW it ended.

        The registry row, the Inbox row (closed through the seam's own resolver on the live
        store) and every open card and list — the ``approval_resolved`` frame is the one signal
        they all act on. Every path that ends an approval comes through here, so none of them
        can leave a surface still asking.

        The frame names the SESSION as well as the id: an open chat drops a frame for another
        session, and matches its card by the ``request_id`` it has always used. ``outcome`` is
        one of :data:`APPROVAL_OUTCOMES` and required, so no path can end an approval without
        stating which of the four things happened; ``approved`` stays on the frame for the
        readers that only need to know whether the call runs.
        """
        if outcome not in APPROVAL_OUTCOMES:
            raise ValueError(f"unknown approval outcome {outcome!r}")
        entry = self._pending_approvals.pop(approval_id, None) or {}
        try:
            from personalclaw.inbox import resolve_attention_items

            resolve_attention_items(self, {"approval": approval_id})
        except Exception:
            self._log.debug("could not close the inbox row for %s", approval_id, exc_info=True)
        try:
            self.broadcast_ws(
                "approval_resolved",
                {
                    "id": approval_id,
                    "request_id": str(entry.get("request_id") or request_id or approval_id),
                    "session": str(entry.get("session") or session),
                    "approved": outcome == "approved",
                    "outcome": outcome,
                },
            )
        except Exception:
            self._log.warning("WS broadcast failed for approval resolution", exc_info=True)

    def end_approval(self, approval_id: str, *, outcome: str) -> None:
        """An approval its waiter stopped waiting for: ``expired`` or ``cancelled``.

        It failed closed, so the call does not run, and every surface says which of the two
        happened. A no-op once a decision has withdrawn it, which is what lets every waiter call
        this unconditionally on its way out.
        """
        if outcome not in UNANSWERED_OUTCOMES:
            raise ValueError(f"{outcome!r} is an answer, not a way to end without one")
        entry = self._pending_approvals.get(approval_id)
        if entry is None:
            return
        self.withdraw_approval(approval_id, outcome=outcome)
        if outcome == "cancelled":
            self._audit_cancelled(approval_id, self._why_cancelled(entry), entry=entry)

    def _why_cancelled(self, entry: dict[str, Any]) -> str:
        """The audit reason for an approval whose waiter was cancelled: the owner's own record
        of how it ended when there is one, else the plain fact that its work was stopped."""
        from personalclaw.dashboard.approval_owner import UNVERIFIABLE, owner_ended

        reason = owner_ended(entry, subagents=self.subagents)
        if reason and reason != UNVERIFIABLE:
            return reason
        return "the work that asked for it was stopped"

    def end_session_approval(
        self, session: "_ChatSession", request_id: str, *, outcome: str
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
        self.end_approval(approval_id, outcome=outcome)

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
        self.withdraw_approval(approval_id, outcome="cancelled")
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
        opening it finds a card whose buttons can no longer deliver an answer. Run when the
        gateway attaches its Inbox, and safe at any other time: an approval still in the
        registry keeps its row.
        """
        from personalclaw.inbox import OPEN_STATUSES, live_store, resolve_attention_items

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
        return sum(resolve_attention_items(self, {"approval": aid}) for aid in sorted(orphaned))

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
        """
        try:
            from personalclaw import notification_kinds, notification_rules, push

            registered = notification_kinds.kind_for_legacy(notification_kinds.APPROVAL)
            rule = notification_rules.resolve_rule(registered.source, registered.kind)
            if rule.mode == "never" or "push" not in rule.targets:
                return
            push.deliver_async("approval", approval_id)
        except Exception:
            self._log.debug("approval push dispatch failed", exc_info=True)

    def resolve_approval(self, approval_id: str, approved: bool) -> bool:
        """Answer a pending approval by its REGISTRY id, from any surface. False if not pending.

        A background origin's future lives here and receives the ``bool`` its gateway waiter
        converts. A chat-held approval is answered by :meth:`decide_session_approval` — the very
        path the chat's own card takes — so a decision made on Home, the phone or the Inbox
        writes the same record and the same audit row, and meets the same refusal handling in
        the waiting runner. There is no second way to answer a chat's approval.

        An approval whose owner has ended is not answered at all: :meth:`refuse_ended_owner`
        cancels it and this returns False, so no door can deliver an Approve to work that is over.
        """
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
                    source="dashboard",
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
        self.decide_session_approval(session, request_id, "approved" if approved else "rejected")
        return True

    def approval_conversation_app(self, approval_id: str) -> str:
        """The app that started the conversation holding the pending approval *approval_id* (by its
        REGISTRY id), or ``""`` — for one of your chats, and for an approval no chat holds."""
        entry = self._pending_approvals.get(approval_id)
        session = self._sessions.get(str(entry.get("session") or "")) if entry else None
        return session.created_by_app if session is not None else ""

    def decide_session_approval(
        self, session: "_ChatSession", request_id: str, action: str
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
        """
        name = session.key
        original_action = action
        grant: dict[str, object] | None = None
        # Trust: auto-approve remaining tools for this session
        if action == "trust":
            session._trust = True
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
        # SEL audit (best-effort — must not block the UI-unblocking path above)
        try:
            sel().log_api_access(
                caller=f"dashboard:{name}",
                operation=f"tool_approval:{original_action}",
                outcome=resolved,
                resources=request_id,
            )
        except Exception:
            self._log.warning("SEL audit failed for approval %s", request_id, exc_info=True)
        return grant
