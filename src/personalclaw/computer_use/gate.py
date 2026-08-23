"""The SEL audit step for desktop computer use (DESKTOP-COMPUTER-USE §3 floor 5, `DCU-2`).

**This module has no opinion.** The plan places it as step 5 of the dispatch chain —
``gate.require_computer_use`` — and is emphatic about what that step is: *"SEL audit
(records, does not decide)"* and *"Every action is SEL-audited (``gate.require_computer_use``
records, doesn't decide)"*. The decisions happened already, upstream: the keystone
(:func:`personalclaw.computer_use.enable_state.is_enabled`, step 1) and the target policy
(``policy.check_app`` / ``policy.check_input_target``, steps 2 and 4). By the time control
reaches here the verdict exists; the only remaining obligation is that it is *written down*.

**The name is the plan's, and the veto is deliberately absent.** ``require_*`` reads like a
gate everywhere else in this codebase, and that is exactly the trap: the next reader will
want to add an ``if not allowed: raise`` here, because the name invites it. Do not. The
refusal a caller needs is :func:`enable_state.disabled_error` (or the policy's), raised by
the layer that made the call; duplicating it here would give one attempt two refusal sites
that can drift, and would make the audit step able to block an operation the policy allowed.
The signature returns ``None`` and the body raises nothing, on any input.

**Why "never decides" implies "never fails".** An audit step that can raise is a
decision-maker by accident: a full disk, a read-only home or a corrupt HMAC key file would
turn "record this" into "refuse this", and a capability the operator armed would collapse for
a reason that has nothing to do with safety. So every failure path here is swallowed —
:func:`require_computer_use` fails **open**.

**Why a swallowed failure is still loud.** A silently total swallow is indistinguishable from
a module that never ran, which would make the atom's clause ("every attempt, allowed or
refused, produces a SEL record") unfalsifiable in production. So a dropped record emits
``logger.warning`` naming the tool and the outcome that did NOT reach the log. WARNING is a
deliberate step up from :func:`enable_state.ensure_computer_use_boot`'s ``logger.debug``:
that record is once-per-run posture evidence, whereas this one is per-attempt, so a
systematically broken audit here is a *silent, ongoing* hole in the security record and must
be visible at the level an operator actually runs.

**What goes in the record, and why ``metadata`` carries no free text.**
:func:`personalclaw.sel.redact_event` exists because *"the log stores a truncated summary of
real tool arguments, so a record can carry a secret a user pasted into a command"* — but two
properties of it matter here. First, it runs only on the way **out** (the forward callback and
the audit read surface); :meth:`SecurityEventLog.log` writes ``asdict(event)`` to disk
unredacted, so anything placed in a field is on disk in the clear regardless. Second, it
delegates to :func:`personalclaw.security.redact`, which recognises *credential*-shaped
strings — not personal data. A computer-use attempt's natural payload is the worst possible
fit for both: a window title ("Bank of America — Checking"), a field label, or the text about
to be typed is personal data that is not credential-shaped, so it would pass ``redact_event``
untouched and sit in the audit log forever. Hence :data:`_safe_metadata`: string and container
values are replaced by a type+length shape summary, so a caller cannot leak user text into the
audit log through this module even by mistake. Keys survive (they are developer-authored
literals) and so do plain scalars, which is all the audit signal an attempt needs — *which*
tool, on *which* app, with *what* verdict. The two fields that do carry prose, ``resources``
and ``error``, take the target app and the refusal's stable code/reason (never tool
arguments), truncated at :data:`personalclaw.sel._MAX_ARG_LEN` like every other SEL writer.
"""

import logging
import uuid
from datetime import datetime, timezone

from personalclaw.sel import SecurityEvent, SecurityEventLog

logger = logging.getLogger(__name__)

#: The SEL ``event_type`` every computer-use attempt carries. One spelling, deliberately.
#:
#: The domain nouns already in the log (``channel_trust``, ``capability_grant``,
#: ``companion_discovery``, ``app_messaging``) are the convention this follows; ``api_access``
#: would be a lie, since driving a desktop accesses no API. It is NOT a second spelling of
#: `DCU-1`'s boot row: that row is ``api_access`` only because it uses the
#: :meth:`SecurityEventLog.log_api_access` convenience method, and it records a different
#: event class — the once-per-run keystone posture, not an attempt. The two stay findable
#: together because both are computer-use rows on one subsystem ``source``.
SEL_EVENT_TYPE = "computer_use"

#: The SEL ``tool_kind`` category for every row this module writes.
#:
#: The plan's prose (§3 tool surface) says each tool "emits a distinct SEL ``tool_kind``",
#: but :class:`personalclaw.sel.SecurityEvent` documents ``tool_kind`` as a *category*
#: ("execute_bash, fs_write, mcp") and ``operation`` as the "tool name". The field
#: definitions win: the per-tool distinctness the plan wants lives in ``operation``, and
#: ``tool_kind`` is the one value that selects the whole capability in a single field filter.
SEL_TOOL_KIND = "computer_use"

#: The SEL ``source`` for a computer-use row when the caller names none.
#:
#: One of :class:`personalclaw.sel.SecurityEvent`'s documented interfaces (channel, dashboard,
#: cli, cron, subagent, background) rather than `DCU-1`'s subsystem-named
#: ``source="computer_use"``: an attempt always arrives *through* an interface, and the
#: capability itself is already named by ``event_type``/``tool_kind``, so spending ``source``
#: on it too would lose the one fact those two do not carry.
#:
#: Set explicitly rather than delegated to :func:`personalclaw.sel._infer_source`, which maps
#: an empty caller to ``"channel"`` — the catch-all its own comment describes as where
#: "unrecognized keys silently land". Recording an unattributed desktop-drive attempt as though
#: a human had typed it in a chat is the one misattribution that matters here, so an un-named
#: attempt is labelled unattended instead. Callers that know better pass ``source``.
_DEFAULT_SOURCE = "background"

#: Reserved ``metadata`` key recording that a caller passed a non-dict ``metadata``.
#: Present rather than dropped: a silently discarded argument is a caller bug nobody can see.
_SHAPE_KEY = "metadata_shape"

#: Longest prose this module writes into ``resources`` / ``error``, matching every other
#: SEL writer (:data:`personalclaw.sel._MAX_ARG_LEN`).
_MAX_LEN = 500


def _shape(value: object) -> str:
    """A leak-proof summary of one value: its type, and its length when it has one."""
    try:
        return f"<{type(value).__name__} len={len(value)}>"  # type: ignore[arg-type]
    except Exception:
        return f"<{type(value).__name__}>"


def _safe_metadata(metadata: object) -> dict:
    """Reduce caller ``metadata`` to values that structurally cannot carry user text.

    Scalars (``bool``/``int``/``float``/``None``) survive verbatim — a count, a flag or an
    element index is exactly the audit signal an attempt needs and cannot be a window title.
    Every other value, including any string and any nested container, is replaced *wholesale*
    by :func:`_shape`. Replacing wholesale rather than walking is the point: there is no depth
    at which a string can survive, so no recursion is needed and none can be got wrong.

    A non-dict ``metadata`` becomes a one-key dict recording its shape, so a caller bug is
    visible in the row itself instead of vanishing.
    """
    if not isinstance(metadata, dict):
        if metadata is None:
            return {}
        return {_SHAPE_KEY: _shape(metadata)}

    def _keep(value: object) -> object:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return _shape(value)

    return {str(key): _keep(value) for key, value in metadata.items()}


def require_computer_use(
    *,
    tool: str,
    app: str = "",
    outcome: str,
    caller_identity: str = "",
    agent: str = "personalclaw",
    source: str = _DEFAULT_SOURCE,
    error: str = "",
    metadata: dict | None = None,
) -> None:
    """Record one computer-use attempt. RECORDS; never decides, never raises.

    The name is the plan's (§3 step 5) even though ``require_*`` reads like a gate — see this
    module's docstring. There is deliberately **no veto**: an ``if`` here that refused
    anything would make the audit step a second decision site behind the keystone and the
    policy, so callers pass the verdict they already reached and this function writes it down.

    ``outcome`` is passed through **verbatim, unvalidated**, even when it is outside
    :class:`personalclaw.sel.SecurityEvent`'s vocabulary (``approved``/``rejected``/``denied``/
    ``completed``/``failed``). Coercing an unrecognised outcome to a known one would record a
    *different verdict than the one that happened*, which corrupts the audit far worse than an
    odd string does; and rejecting it would be a decision. A refused attempt is
    ``outcome="denied"`` with the refusal's stable code in ``error`` (e.g.
    :data:`enable_state.ERR_DISABLED`), so allowed and refused rows are one query apart.

    Returns ``None`` on every input, including a failed SEL write — see the module docstring
    on failing open and on why the drop is logged at WARNING.
    """
    try:
        target = str(app)
        SecurityEventLog().log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type=SEL_EVENT_TYPE,
                caller_identity=str(caller_identity),
                agent=str(agent),
                source=str(source) or _DEFAULT_SOURCE,
                operation=str(tool),
                tool_kind=SEL_TOOL_KIND,
                outcome=str(outcome),
                resources=f"app={target}"[:_MAX_LEN] if target else "",
                error=str(error)[:_MAX_LEN],
                metadata=_safe_metadata(metadata),
            )
        )
    except Exception:
        # Fail OPEN: see the module docstring. An audit that can raise is a decision-maker,
        # and this module is not allowed to be one. WARNING (not debug) because this line is
        # the ONLY remaining evidence that the attempt happened — at debug level a
        # systematically broken audit would be indistinguishable from a working one.
        logger.warning(
            "computer_use SEL audit DROPPED — attempt is NOT in the audit log "
            "(tool=%r outcome=%r app=%r)",
            tool,
            outcome,
            app,
            exc_info=True,
        )
