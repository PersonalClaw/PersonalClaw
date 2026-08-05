"""The outbound delivery contract: statusUrl, stable event ids, destination formatting (R18 — S85).

Criterion 10: "A completed-run notification deep-links (statusUrl) to the exact run journal row; a
retried delivery does not double-ping."

**Measured before writing.** A grep for `statusUrl` or `status_url` across `src/personalclaw`
returns **nothing** — the deep link the criterion names does not exist anywhere in the package.
(Stated as prose rather than as the literal grep pattern: a backslash-pipe alternation inside a
non-raw docstring is an invalid escape sequence, and Python warns on import. Caught by running
the module, not by reading it.) A completed-run notification carries a title and a body, so a
user reading "Nightly digest finished" has no route to the run that produced it. R18 calls that
"the notification→journal dead end".

Three things this owns, all from R18's own list:

* **`statusUrl`** — `#/workflows/runs/<run_id>` for a workflow run, `#/triggers?open=<id>` for
  a fire
  with no run behind it. Verified against the live routes: `WorkflowsSection` documents
  `#/workflows/runs/<run_id>`, and `TriggersListPage` opens its side panel from `?open=<id>`.
* **A stable event id preserved across retries** — the idempotency key a channel consumer
  dedupes on.
  Derived, not random: a `uuid4()` would be a *different* id on the retry, which is precisely the
  double-ping the criterion forbids.
* **Destination-aware formatting** — a rich block for inbox/notify, flattened text for
  `channel:slack`. Slack renders a dict as `[object Object]`; the flattening is not cosmetic.

**This does NOT build a second notification path.** R18 is explicit that delivery routes through
`DashboardState.notify` → `notification_allowed()`. Everything here produces the ARGUMENTS for
that call — `kind`, `title`, `body`, `meta` — and `meta` is the dict `notify` already merges
into the note, so `statusUrl` reaches every surface without widening a schema. The redaction R18
requires happens on the way in, because a run summary can contain a URL or a token from whatever
the run touched.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: R18's event types. Two, not one with a boolean: a channel consumer routes on the event name, and
#: `automation.run` + `{"ok": false}` would make "only tell me about failures" a body inspection.
EVENT_SUCCEEDED = "automation.run.succeeded"
EVENT_FAILED = "automation.run.failed"

#: Destinations that render structured blocks vs flattened text. `channel:slack` is prefix-matched
#: because the channel id carries a workspace suffix in practice (`channel:slack:T0123`), and an
#: exact match would silently fall back to rich blocks for every real Slack destination.
_FLAT_TEXT_PREFIXES = ("channel:",)

#: Max body characters in a delivered notification. A run summary is model output; an unbounded one
#: pushes the statusUrl off the bottom of a Slack card, which defeats the deep link this session
#: exists to add.
BODY_CAP = 600


def status_url(*, run_id: str = "", trigger_id: str = "") -> str:
    """The deep link into the exact run journal row, or the trigger that fired.

    A RUN id wins when both are present: R18 says "the exact runs-inbox row / run journal", and
    the run is the specific thing that just happened. Falling back to the trigger matters for
    the `LEDGER`-weight fires that never produce a run directory (a suppressed or noop fire) — a
    notification about one of those still needs somewhere to go, and pointing at the trigger is
    honest where pointing at a nonexistent run would 404.

    Returns "" when neither is known rather than a bare `#/` — a link that goes to the dashboard
    root tells the user nothing and costs them a click to discover that.
    """
    if run_id:
        return f"#/workflows/runs/{run_id}"
    if trigger_id:
        return f"#/triggers?open={trigger_id}"
    return ""


def event_id(*, trigger_id: str, run_id: str = "", attempt_key: str = "") -> str:
    """The stable id a retried delivery reuses. DERIVED, never random.

    R18: "a stable event-id preserved across retries (the idempotency key — channel consumers dedupe
    re-delivered notifications)". So the id is a hash of what identifies the EVENT, not of when
    the delivery happened: a `uuid4()` or a timestamp would produce a new id on the retry and
    the consumer would show the notification twice, which is the exact failure the criterion
    names.

    `attempt_key` is for the case where a re-run genuinely IS a new event — a manual re-fire of
    the same trigger should ping again. Callers pass the run's epoch or attempt number; leaving it
    empty means "the same event", which is the safe default for a transport retry.
    """
    basis = "|".join([trigger_id or "", run_id or "", attempt_key or ""])
    return "evt_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def _redact(text: str) -> str:
    """R18's redaction, applied before ANY surface — as heartbeat delivery does today.

    A run summary is whatever the run produced: it can contain a URL the run fetched or a token a
    tool printed. Redacting at the delivery boundary rather than at each emitter is what makes the
    guarantee hold for an emitter nobody has written yet.
    """
    try:
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        out, _ = redact_exfiltration_urls(text or "")
        out, _ = redact_credentials(out)
        return out
    except Exception:  # noqa: BLE001 - redaction must never drop the notification
        logger.debug("delivery redaction failed; sending the untouched text", exc_info=True)
        return text or ""


@dataclass
class Delivery:
    """One outbound run-completion notification, ready for `DashboardState.notify`.

    Carries the notify ARGUMENTS rather than sending anything: R18 forbids a second notification
    path, and a dataclass that delivered itself would be one. `to_notify_kwargs` is the whole
    interface.
    """

    event: str
    event_id: str
    title: str
    body: str
    status_url: str = ""
    trigger_id: str = ""
    run_id: str = ""
    destination: str = ""
    #: The notification kind the gate checks. Defaults per outcome in `build_delivery`.
    kind: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.event == EVENT_SUCCEEDED

    def to_notify_kwargs(self) -> dict[str, Any]:
        """`**kwargs` for `state.notify(kind, title, body, meta=...)`.

        Everything routable lives in `meta`, which `notify` merges into the persisted note — so a
        surface reads `statusUrl` off the notification without `InboxItem` or the note schema
        gaining a field. The same reason S51's structured card rides `refs`.
        """
        meta: dict[str, Any] = {
            "event": self.event,
            # camelCase because R18 names it `statusUrl` and a channel consumer reads the wire key.
            "statusUrl": self.status_url,
            "eventId": self.event_id,
            **self.meta,
        }
        if self.trigger_id:
            meta["trigger_id"] = self.trigger_id
        if self.run_id:
            meta["run_id"] = self.run_id
        return {"kind": self.kind, "title": self.title, "body": self.body, "meta": meta}

    def to_text(self) -> str:
        """The flattened form for a text destination.

        The statusUrl is appended as a LINE rather than embedded in prose: a Slack consumer that
        auto-links bare URLs makes it clickable, and a user scanning a wall of text finds a trailing
        link faster than one buried mid-sentence.
        """
        parts = [self.title]
        if self.body:
            parts.append(self.body)
        if self.status_url:
            parts.append(self.status_url)
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "event_id": self.event_id,
            "title": self.title,
            "body": self.body,
            "status_url": self.status_url,
            "trigger_id": self.trigger_id,
            "run_id": self.run_id,
            "destination": self.destination,
            "kind": self.kind,
            "ok": self.ok,
            "meta": dict(self.meta),
        }


def wants_flat_text(destination: str) -> bool:
    """Whether this destination needs flattened text instead of a structured block.

    Prefix-matched on `channel:` — the id carries a workspace suffix in practice, and an exact
    `== "channel:slack"` would send rich blocks to every real Slack destination, which renders as
    `[object Object]`.
    """
    dest = (destination or "").strip().lower()
    return any(dest.startswith(p) for p in _FLAT_TEXT_PREFIXES)


def build_delivery(
    *,
    trigger_id: str,
    trigger_name: str = "",
    ok: bool,
    summary: str = "",
    run_id: str = "",
    destination: str = "",
    attempt_key: str = "",
    duration_secs: float = 0.0,
) -> Delivery:
    """Assemble one run-completion delivery. Pure.

    The notification KIND is chosen per outcome, not per emitter: a failure has to be able to
    escalate past a "digest" rule while a success should not, and that is a property of what
    happened rather than of who is reporting it. Both names come from `notification_kinds` so the
    user's existing rules apply — inventing a kind here would produce a notification no rule
    matches, which resolves to `immediate` and ignores the user's settings.
    """
    from personalclaw import notification_kinds

    name = trigger_name or trigger_id or "automation"
    event = EVENT_SUCCEEDED if ok else EVENT_FAILED
    kind = notification_kinds.INFO if ok else notification_kinds.ERROR
    verb = "finished" if ok else "failed"
    title = _redact(f"{name} {verb}")
    body = _redact(summary or "")[:BODY_CAP]
    meta: dict[str, Any] = {}
    if duration_secs:
        meta["duration_secs"] = round(float(duration_secs), 3)
    return Delivery(
        event=event,
        event_id=event_id(trigger_id=trigger_id, run_id=run_id, attempt_key=attempt_key),
        title=title,
        body=body,
        status_url=status_url(run_id=run_id, trigger_id=trigger_id),
        trigger_id=trigger_id,
        run_id=run_id,
        destination=destination,
        kind=kind,
        meta=meta,
    )


def route_for(trigger: Any, *, ok: bool) -> str:
    """The destination this OUTCOME routes to (decision 13 / R12 — S158).

    🔴 WHY THIS EXISTS. `Trigger.failure_delivery` is declared, persisted, round-tripped by
    `to_dict`/`from_dict`, defaulted by the migration and accepted by `automation_update` — and read
    by NOTHING. Its own comment states the contract it was meant to enforce: *"A SEPARATE route for
    failures (R12). Failures reach the inbox even when `delivery` is none: an automation the user
    asked to stay quiet still has to be able to say it broke."*

    Measured before writing: `_deliver_fire_outcome` passed `destination=trigger.delivery`
    unconditionally, so the failure route was never consulted — and a `delivery: "none"` automation
    that broke reported its failure through the silent channel.

    Falls back to `delivery` when `failure_delivery` is empty, and only for a FAILURE — a success
    must never inherit the failure route, or a quiet automation would start announcing its ordinary
    runs through the inbox.
    """
    if ok:
        return str(getattr(trigger, "delivery", "") or "none")
    failure = str(getattr(trigger, "failure_delivery", "") or "")
    return failure or str(getattr(trigger, "delivery", "") or "none")


#: Volatile patterns stripped before hashing a failure for dedup: ISO timestamps and UUIDs. Without
#: this, the same outage produces a fresh hash every minute simply because its message carries the
#: clock, and dedup never fires.
_VOLATILE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"  # ISO timestamps
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",  # UUIDs
    re.IGNORECASE,
)
_EPOCH_RE = re.compile(r"\b\d{10,13}\b")
_EPOCH_WINDOW_SECS = 300  # strip epoch values within ±5 min of now


def failure_hash(text: str) -> str:
    """Normalize volatile data and return a 16-hex-char SHA-256 prefix.

    Strips ISO timestamps, UUIDs, and any 10-13 digit number that looks like an epoch within ±5
    minutes of now. Non-epoch numeric ids (account ids, build ids) are preserved because they fall
    outside the window — two failures differing only in a build id are genuinely different failures.

    Truncated to 64 bits: sufficient for a 1:1 comparison against a single previous hash.

    MOVED here from `gateway._result_hash` (S161), which had been left with **zero callers**
    when the legacy failure-dedup control was lost in the migration — along with four other
    orphaned constants. It lives beside its only reader now rather than in the orchestrator,
    so a `triggers` module does not have to import the gateway.
    """
    now = time.time()
    lo = now - _EPOCH_WINDOW_SECS
    hi = now + _EPOCH_WINDOW_SECS

    def _strip_epoch(m: "re.Match[str]") -> str:
        v = int(m.group())
        ts = v / 1000 if v > 9_999_999_999 else v  # 13 digits → millis
        return "" if lo <= ts <= hi else m.group()

    text = _VOLATILE_RE.sub("", text)
    text = _EPOCH_RE.sub(_strip_epoch, text)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


#: How long an IDENTICAL failure stays deduped before it re-alerts (R7's `dedupe_hash`).
#:
#: 3600s, carried over verbatim from the legacy scheduler's `_FAILURE_REMINDER_SECS` — a
#: constant that survived the migration in `gateway.py` and had **no reader left**, because the
#: control it belonged to was lost. Preserved rather than re-chosen: an operator who learned
#: "a broken cron nags me hourly" should not have that change silently.
FAILURE_REMINDER_SECS = 3600.0


def suppress_repeat_failure(
    *, error: str, last_hash: str, last_at: float, now: float
) -> tuple[bool, str]:
    """Whether this failure repeats the last one inside the reminder window (R7 — S161).

    Returns `(suppress, hash_of_this_error)`. The caller persists the hash either way, so a
    NEW error resets the window rather than inheriting the old one's.

    🔴 WHY THIS EXISTS. `failure_policy.dedupe_hash` is written by the migration from the
    legacy `last_failure_hash` and read by nothing — the last unread key on that field. The
    legacy `gateway.py` had the whole control (`is_dup = fh == job.last_failure_hash` inside a
    1h window, with `consecutive_failures` still advancing so autopause was unaffected); the
    unified fire path kept the constant and dropped the check. Measured: a trigger failing with
    the SAME error on 6 consecutive fires produced **6 notifications**, because `event_id`
    dedupes the same event REDELIVERED (same `run_id`), not different fires with one error.

    **Hashed from the ERROR TEXT, not from `last_error_summary`.** That field holds
    `PauseDecision.reason` — `"failure 1 of 5"`, `"failure 2 of 5"` — which changes on every
    failure even when the cause is identical, so hashing it could never dedupe once.

    Volatile data is normalised by `failure_hash` (timestamps, UUIDs, epoch-looking numbers),
    so one outage does not mint a fresh hash every minute because its message carries a clock.

    **Suppression is capped by the window, never unbounded**: a still-broken automation
    re-alerts hourly, because "it stopped telling me" and "it got fixed" must not look alike.
    An empty error or a missing prior hash never suppresses — the first alert always goes out.
    """
    text = (error or "").strip()
    if not text:
        return False, ""
    digest = failure_hash(text)
    if not last_hash or digest != last_hash:
        return False, digest
    if last_at <= 0:
        return False, digest
    return (now - last_at) < FAILURE_REMINDER_SECS, digest


def is_muted(destination: str) -> bool:
    """Whether this destination means "do not notify" (decision 13's `none`).

    🔴 THE SECOND HALF. `Delivery` carried `destination` and `to_notify_kwargs` **dropped it**, so
    `delivery: "none"` silenced nothing: measured, a `none` trigger notified exactly like an `inbox`
    one. The field existed, round-tripped, and was inert at the only point that could honour it.

    Checked HERE rather than by teaching `state.notify` about triggers. R18 says *"the
    substrate does not build a second notification path"*, and `notify` owns GLOBAL policy
    (mute-all, severity, quiet hours) plus per-(source, kind) rules. Per-TRIGGER routing is
    this substrate's own concern, so it is decided before the shared chokepoint is called,
    never inside it.

    An empty destination is NOT muted: `from_dict` defaults `delivery` to `"none"` explicitly, so a
    blank value means a caller built a `Delivery` without one, and defaulting that to silence would
    let a bug turn into missing alerts.
    """
    return str(destination or "").strip().lower() == "none"


def is_duplicate(delivery: Delivery, delivered_ids: "set[str] | list[str] | None") -> bool:
    """Whether this delivery has already gone out — the "does not double-ping" half.

    Checked on `event_id`, which is stable across retries by construction. The caller owns the
    seen-set because the retry window is a transport concern (an in-memory set for a process, a
    persisted one for a channel), and baking a store in here would make this module stateful for
    no reader.
    """
    return bool(delivered_ids) and delivery.event_id in set(delivered_ids or ())


def deliver(state: Any, delivery: Delivery, *, delivered_ids: Any = None) -> bool:
    """Send one delivery through `state.notify`. Returns True if it went out.

    Routes through the EXISTING gate by construction — this calls `notify`, which applies
    `notification_allowed` and then the per-(source, kind) rule. R18: "the substrate does not
    build a second notification path."

    Adds the event id to `delivered_ids` when the caller supplies a mutable set, so a retry
    through the same set is suppressed without the caller having to remember to record it. Never
    raises: a notification failure must not fail the run that completed, which already happened.
    """
    if state is None:
        return False
    # 🔴 HONOUR `destination: none` (S158). `to_notify_kwargs` drops `destination`, so before this
    # check a muted automation notified exactly like an `inbox` one. Enforced HERE rather than at
    # each caller so a future emitter inherits it — as redaction already does at this boundary.
    if is_muted(delivery.destination):
        logger.debug("delivery %s suppressed: destination is none", delivery.event_id)
        return False
    if is_duplicate(delivery, delivered_ids if isinstance(delivered_ids, (set, list)) else None):
        logger.debug("delivery %s already sent; not double-pinging", delivery.event_id)
        return False
    try:
        state.notify(**delivery.to_notify_kwargs())
    except Exception:  # noqa: BLE001 - the run already completed; a failed ping must not undo it
        logger.debug("delivery %s could not be sent", delivery.event_id, exc_info=True)
        return False
    if isinstance(delivered_ids, set):
        delivered_ids.add(delivery.event_id)
    return True
