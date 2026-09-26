"""Data-event triggers (#38): the event bus and the pattern grammar of `kind: "event"` rows.

An event trigger is an ordinary row in the one trigger store (`triggers.json`) with
``kind: "event"``. It fires when PersonalClaw's own state changes. The vocabulary is
**source-agnostic** (EIAT-1): every event carries a ``source``, and a trigger only ever fires on
events of its own source.

Memory writes (``vector_memory._log_event``) — ``source="memory"``:

- **MemoryUpdate**     — any memory write (create/update/delete).
- **MemoryKeyPattern** — a write whose key matches a glob (``project.acme.*``).
- **ContentMatch**     — a write whose value matches a regex/substring.

Inbox messages (``inbox_service._ingest``, after the allowlist) — ``source="inbox"``:

- **InboxMessage**     — any accepted inbox message from a watched source.
- **InboxSender**      — a message whose sender matches ``sender_glob``.
- **InboxAddress**     — a message whose receiving address matches ``address_glob``.

App-contributed sources (``trigger_sources.emit``, AUTO-A4) — ``source="app"``:

- **AppEvent**         — an event from an installed app's ``trigger_source`` provider, whose
  namespaced name (``app:<app>:<event>``) matches ``event_glob``. An empty glob matches every app
  event. The payload is fenced at ingestion with the app's provenance, so the app's own text can
  never arrive as instructions.

A row's ``spec`` is ``{source, pattern}`` plus the ONE matcher key its pattern reads
(:data:`PATTERN_MATCHER`); ``source`` is derived from the pattern, never chosen. Its budget and
burst guard are ordinary gates — ``gates.max_fires`` ("alert me the NEXT time X") and
``gates.debounce_secs`` — enforced by the same gate walk a clock fire takes.

**How an event reaches an action.** Every source calls the ONE emitter, :func:`emit_event`,
best-effort. In the gateway process a router is attached (:func:`attach`,
``triggers.event_fire.EventRouter``): it matches the event against the store's ``event`` rows,
admits each match through ``triggers.service.admit_fire`` (incident, spacing, rate, quiet, duty,
budget, claim, capability — the clock fire's walk), and runs it through the one store dispatch,
``gateway._fire_store_trigger``: injection screen, fence, ``{{secret:…}}`` resolution, denylist,
rung routing, day budget, the run record, autopause and delivery. In any other process — the CLI,
the ``mcp-core`` server an agent's memory tools run in, a script — there is no router, so an event
some stored trigger wants is parked in the dispatch spool, and the gateway's next tick re-emits it
there.

🔴 WHY THIS SHAPE (measured on main before it was written). This module used to own a SECOND
store (``event_triggers.json``), a second engine and a second dispatch seam. A fire there ran its
action and recorded nothing — the trigger's history answered ``supported: false`` — and it ran in
whichever process wrote the memory, so an agent's memory write fired a ``notify`` inside the
``mcp-core`` subprocess, where no dashboard exists to show it. Meanwhile the ``kind: "event"`` rows
the chat's ``automation_create`` wrote into ``triggers.json`` were matched by nothing at all. One
store, one matcher, one dispatch replaces both; ``boot_migrate.absorb_event_triggers`` imports a
legacy ``event_triggers.json`` once, at boot.
"""

from __future__ import annotations

import fnmatch
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Event sources (EIAT-1 C1). A source is the CLASS of origin — matching is scoped by it, so a
# memory trigger can never fire on an inbox event and vice versa.
SOURCE_MEMORY = "memory"
SOURCE_INBOX = "inbox"
#: App-contributed sources (AUTO-A4). Its PRODUCER is `trigger_sources.registry.emit`, the single
#: ingestion point that namespaces the event (`app:<app>:<event>`) from the app's registered name
#: and fences its text at origin.
SOURCE_APP = "app"
EVENT_SOURCES = (SOURCE_MEMORY, SOURCE_INBOX, SOURCE_APP)

# Event-pattern kinds. The three memory patterns are KEPT VERBATIM — they are persisted values, and
# renaming them would silently retire every stored trigger.
MEMORY_UPDATE = "MemoryUpdate"
MEMORY_KEY_PATTERN = "MemoryKeyPattern"
CONTENT_MATCH = "ContentMatch"
# Inbox patterns (EIAT-1). InboxSender/InboxAddress read the meta dict a source supplies, so the
# bus never learns any source's schema.
INBOX_MESSAGE = "InboxMessage"
INBOX_SENDER = "InboxSender"
INBOX_ADDRESS = "InboxAddress"
# App-source pattern (AUTO-A4). ONE pattern, not a catch-all plus a glob pattern: both would read
# the same `event_type`, so the catch-all is just an empty `event_glob`.
APP_EVENT = "AppEvent"
EVENT_PATTERNS = (
    MEMORY_UPDATE,
    MEMORY_KEY_PATTERN,
    CONTENT_MATCH,
    INBOX_MESSAGE,
    INBOX_SENDER,
    INBOX_ADDRESS,
    APP_EVENT,
)

#: Which source each pattern belongs to. A pattern is only ever evaluated for events of its own
#: source; the writer derives `spec.source` from this table and validation refuses a row whose
#: stored source disagrees with it.
PATTERN_SOURCE: dict[str, str] = {
    MEMORY_UPDATE: SOURCE_MEMORY,
    MEMORY_KEY_PATTERN: SOURCE_MEMORY,
    CONTENT_MATCH: SOURCE_MEMORY,
    INBOX_MESSAGE: SOURCE_INBOX,
    INBOX_SENDER: SOURCE_INBOX,
    INBOX_ADDRESS: SOURCE_INBOX,
    APP_EVENT: SOURCE_APP,
}

#: The ONE spec key each pattern reads, or None for a pattern that fires on every event of its
#: source. A spec carries only this key: the create form shows exactly this field, and any other
#: matcher on a row is inert for its pattern, which validation reports.
PATTERN_MATCHER: dict[str, str | None] = {
    MEMORY_UPDATE: None,
    MEMORY_KEY_PATTERN: "key_glob",
    CONTENT_MATCH: "content_re",
    INBOX_MESSAGE: None,
    INBOX_SENDER: "sender_glob",
    INBOX_ADDRESS: "address_glob",
    APP_EVENT: "event_glob",
}

#: Every matcher key a spec may carry.
MATCHER_KEYS: frozenset[str] = frozenset(k for k in PATTERN_MATCHER.values() if k)

#: Patterns whose matcher must be non-empty. An empty one is a trigger that can never do what its
#: author chose it for: an empty `sender_glob`/`address_glob` would narrow nothing (and the matcher
#: treats an empty glob as matching nothing at all), and an empty `key_glob`/`content_re` matches
#: no write, ever. `AppEvent` is the exception on purpose — its empty glob is the documented
#: catch-all across every app event.
REQUIRED_MATCHER: frozenset[str] = frozenset(
    {MEMORY_KEY_PATTERN, CONTENT_MATCH, INBOX_SENDER, INBOX_ADDRESS}
)

#: The burst guard every new event trigger starts with (`gates.debounce_secs`), unless its author
#: sets another (0 turns it off). Carried over from the retired engine, which applied it to every
#: row: one logical change often reaches the bus as several events — a memory updated twice in one
#: save, a consolidation pass writing a batch — and one run is what the author meant.
DEFAULT_DEBOUNCE_SECS = 5.0

#: The `$variables` an event trigger's action can use — exactly what :func:`fire_payload` builds.
#: Co-located with the builder so the catalog the create form reads cannot drift from the payload.
EVENT_VARS: tuple[str, ...] = (
    "$EVENT",
    "$CONTEXT",
    "$source",
    "$event_type",
    "$key",
    "$value",
)


def event_spec(pattern: str, matcher: str = "") -> dict[str, str]:
    """The canonical spec for ``pattern`` and its one matcher value.

    ``source`` is DERIVED from the pattern, never taken from a caller: a supplied source could
    contradict the pattern and defeat the isolation the source gate exists to enforce. An empty
    matcher is omitted rather than stored blank, so validation reports a missing required one.
    """
    spec = {"source": PATTERN_SOURCE.get(pattern, ""), "pattern": pattern}
    field = PATTERN_MATCHER.get(pattern)
    if field and matcher:
        spec[field] = matcher
    return spec


def with_derived_source(spec: dict[str, Any]) -> dict[str, Any]:
    """``spec`` with ``source`` filled in from its pattern when the author left it out.

    For the writers that take a spec as given (the chat's ``automation_create``): an agent that
    names a pattern has said which source it means, and requiring it to repeat that is a round
    trip for nothing. A source that IS present is kept as written, so a contradiction reaches
    validation and is refused rather than silently corrected.
    """
    out = dict(spec or {})
    pattern = str(out.get("pattern") or "")
    if not str(out.get("source") or "").strip() and pattern in PATTERN_SOURCE:
        out["source"] = PATTERN_SOURCE[pattern]
    return out


#: How much of a memory value `ContentMatch` will scan.
#:
#: §7/R4 rule (d) — "payload content never participates in event-pattern matching; only trigger spec
#: patterns match, payload is data" — HOLDS here: the regex comes from the trigger's spec and the
#: value is only ever matched against.
#:
#: 4 KB because a `ContentMatch` trigger asks "does this memory value mention X", and a mention that
#: first appears past 4 KB is not what anyone is watching for. Applied to the SCAN only, never to
#: what is stored or fired.
#:
#: 🔴 **THIS CAP DOES NOT FIX ReDoS.** Measured on this very function: an author regex of `(a+)+$`
#: takes 0.66s at 24 characters, 2.5s at 26, 10.2s at 28, 40.7s at 30 — exponential in length, so
#: a 4096-char cap bounds nothing useful. Catastrophic patterns are addressed where they are
#: authored — see `catastrophic_regex_hint`.
CONTENT_MATCH_SCAN_LIMIT = 4096

#: Regex constructs whose backtracking is exponential: a quantifier applied to a group that is
#: itself quantified (`(a+)+`, `(a*)*`, `(a+)*`) or an alternation-in-a-quantified-group (`(a|a)+`).
_CATASTROPHIC_RE = re.compile(r"\([^)]*[+*]\)[+*]|\((?=[^)]*\|)[^)]*\)[+*]")


def catastrophic_regex_hint(pattern: str) -> str:
    """A warning if `pattern` has exponential-backtracking shape, else "".

    Detection at AUTHOR time rather than a timeout at match time: Python's `re` has no timeout,
    and a thread cannot be killed mid-regex. So the residual risk is stated plainly — a user who
    saves a catastrophic pattern and dismisses this warning can stall their own memory-write path.
    That is a self-inflicted local slowdown, not a remote DoS, and refusing the pattern outright
    would break working triggers.
    """
    if not pattern or not _CATASTROPHIC_RE.search(pattern):
        return ""
    return (
        "this pattern nests a quantifier inside a quantified group (e.g. `(a+)+`), which "
        "backtracks exponentially — a 30-char value can take ~40s, on the memory-write path. "
        "Simplify it (`(\\w+)+` almost always means `\\w+`)"
    )


def matches(
    trigger: Any,
    *,
    source: str,
    event_type: str,
    key: str,
    value: str,
    meta: dict | None = None,
) -> bool:
    """Pure: does the ``kind: "event"`` row ``trigger`` match this event?

    ``fires_automatically`` first — enabled AND active, asked as ONE question, because checking
    ``enabled`` alone is how a parked or autopaused trigger keeps firing. Then the source gate,
    before any pattern logic: a trigger listens to exactly one source, and this gate — not the
    pattern table — is what makes cross-source firing impossible. The fire BUDGET is not checked
    here: it is the gate walk's ``budget`` gate, which records the suppression instead of dropping
    it.

    §7/R4 rule (d): only the trigger SPEC supplies patterns. The value's scan length is capped — see
    `CONTENT_MATCH_SCAN_LIMIT`.
    """
    if getattr(trigger, "kind", "") != "event" or not getattr(
        trigger, "fires_automatically", False
    ):
        return False
    spec = trigger.spec if isinstance(getattr(trigger, "spec", None), dict) else {}
    if str(spec.get("source") or "") != source:
        return False
    pattern = str(spec.get("pattern") or "")
    # A stored pattern that belongs to another source can never match: the pair is a contradiction
    # validation refuses, and reading it here as "matches its stored source" would fire it anyway.
    if PATTERN_SOURCE.get(pattern) != source:
        return False
    field = PATTERN_MATCHER.get(pattern)
    wanted = str(spec.get(field) or "") if field else ""
    if pattern in (MEMORY_UPDATE, INBOX_MESSAGE):
        return True
    if pattern == MEMORY_KEY_PATTERN:
        return bool(wanted) and fnmatch.fnmatch(key or "", wanted)
    if pattern == CONTENT_MATCH:
        if not wanted:
            return False
        # Bounded BEFORE the regex sees it: this is the function every caller reaches.
        scanned = (value or "")[:CONTENT_MATCH_SCAN_LIMIT]
        try:
            return re.search(wanted, scanned) is not None
        except re.error:
            return wanted in scanned
    if pattern == APP_EVENT:
        # Matched on the NAMESPACED `event_type` (`app:<app>:<event>`), which core derives from the
        # app's REGISTERED name at ingestion — so a glob of `app:calendar:*` cannot be tripped by a
        # hostile app claiming to be `calendar`. An empty glob is the catch-all.
        return not wanted or fnmatch.fnmatch(event_type or "", wanted)
    meta = meta or {}
    if pattern == INBOX_SENDER:
        return bool(wanted) and fnmatch.fnmatch(str(meta.get("sender") or ""), wanted)
    if pattern == INBOX_ADDRESS:
        return bool(wanted) and fnmatch.fnmatch(str(meta.get("address") or ""), wanted)
    return False


# ── one event, and what a fire hands the store dispatch ──


@dataclass(frozen=True)
class BusEvent:
    """One event on the bus, as a source reported it."""

    source: str
    event_type: str
    key: str
    value: str
    now: float
    meta: dict | None = None

    def matches(self, trigger: Any) -> bool:
        return matches(
            trigger,
            source=self.source,
            event_type=self.event_type,
            key=self.key,
            value=self.value,
            meta=self.meta,
        )


def _truncate_fenced(value: str, limit: int) -> str:
    """Truncate text that is ALREADY fenced, keeping the fence closed (AUTO-A4).

    Cutting a fenced span can remove its closing marker, and an UNTERMINATED fence is worse than a
    truncated one: everything the model reads after it falls outside the fence. So the close is
    re-appended when the cut removed it.
    """
    from personalclaw.security import UNTRUSTED_CLOSE

    cut = value[:limit]
    return cut if UNTRUSTED_CLOSE in cut else f"{cut}\n{UNTRUSTED_CLOSE}"


def fire_payload(trigger_id: str, event: BusEvent) -> tuple[dict[str, Any], str]:
    """What one matched event hands the store dispatch: ``(payload, context)``.

    The value is FENCED HERE, AT ORIGIN, for every fire — a memory value, an inbox message and an
    app's text are all untrusted by definition. At origin because provenance is three claims (§7/R4
    rule c — S127): the CLASS of origin, WHICH one, and HOW it got here. "an event said this" and
    "THIS memory key said it, truncated to 2000 chars" differ, and only the second lets a reader
    tell whether the text the model acted on is the text that arrived. The store dispatch's own
    ``screen.fence_payload`` is idempotent, so it leaves this richer fence alone — the precedent
    ``web_watch`` items and app events already set. Measured before relying on it: the injection
    screen returns the same verdict on the fenced text as on the raw text for every payload in the
    shipped corpus (78 of 78), so screening after the fence loses nothing.

    Text already fenced at origin (an app event, fenced by ``trigger_sources.emit`` with the app's
    own provenance) is truncated but NOT re-wrapped: re-wrapping escapes the inner markers, so the
    origin's attributes would read to the model as literal text.

    ``context`` is the ``$CONTEXT`` line a template renders: ``<key>: <fenced 200-char excerpt>``.
    """
    from personalclaw.security import fence_untrusted, is_fenced

    value = event.value or ""
    if is_fenced(value):
        fenced = _truncate_fenced(value, 2000)
        excerpt = _truncate_fenced(value, 200)
    else:
        fenced = fence_untrusted(
            value[:2000],
            source=f"trigger:{trigger_id}:{event.source}:{event.event_type}",
            source_type=f"event:{event.source}:{event.event_type}",
            source_id=event.key,
            transformation_path="truncate:2000",
        )
        excerpt = fence_untrusted(
            value[:200],
            source=f"trigger:{trigger_id}",
            source_type="event",
            source_id=event.key,
            transformation_path="truncate:200",
        )
    payload: dict[str, Any] = {
        "trigger_id": trigger_id,
        "source": event.source,
        "event_type": event.event_type,
        "key": event.key,
        "value": fenced,
    }
    if event.meta:
        payload["meta"] = dict(event.meta)
    return payload, f"{event.key}: {excerpt}"


# ── the bus ──

#: The process's router, attached by the gateway. `None` everywhere else.
Router = Callable[[BusEvent], None]
_router: Router | None = None
_router_lock = threading.Lock()


def attach(router: Router) -> None:
    """Make ``router`` the one place this process's events go. The gateway calls this at boot.

    A router must be safe to call from any thread and must never raise: a source calls the bus from
    its own write path, and a memory write is the user's actual work.
    """
    global _router
    with _router_lock:
        _router = router


def detach(router: Router | None = None) -> None:
    """Remove the router — only ``router`` itself when one is named, so a stale shutdown cannot
    detach a router a newer gateway in the same process attached."""
    global _router
    with _router_lock:
        if router is None or _router is router:
            _router = None


def router_attached() -> bool:
    """Whether this process has a router — i.e. whether :func:`emit_event` DELIVERS here.

    The spool drain asks before re-emitting an event: in a process without one, re-emitting would
    only spool the event again, forever.
    """
    return _router is not None


def emit_event(
    *,
    source: str,
    event_type: str,
    key: str,
    value: str | None,
    now: float,
    meta: dict | None = None,
) -> None:
    """The ONE emitter every source calls after an event (EIAT-1). Best-effort, never raises.

    ``source`` (``SOURCE_MEMORY``/``SOURCE_INBOX``/``SOURCE_APP``) scopes which triggers can fire,
    and ``meta`` carries source-specific fields (an inbox message's ``sender``/``address``) the
    inbox patterns match. A trigger fault must never break the source's own work, so every failure
    here is logged and swallowed.
    """
    try:
        event = BusEvent(
            source=source,
            event_type=event_type,
            key=key,
            value=value or "",
            now=now,
            meta=dict(meta) if meta else None,
        )
        router = _router
        if router is not None:
            router(event)
            return
        _spool_if_wanted(event)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.debug("emit_event failed", exc_info=True)


def _spool_if_wanted(event: BusEvent) -> None:
    """No router in this process: park the event for the gateway, if a stored trigger wants it.

    This is the path an agent's memory write takes — its tools run in the ``mcp-core`` subprocess —
    and a CLI's. Parking every event would grow the spool on every write; parking only a wanted one
    keeps it to the fires that will actually happen. The gateway re-matches on drain, against the
    store as it is then, so a trigger edited in between is judged as it now stands.

    The envelope carries the EVENT, not a trigger: the drain re-emits it through :func:`emit_event`
    in the gateway, where the router matches every row once. One envelope per event is also what
    lets the spool's content-hash dedup collapse an identical event reported twice.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.triggers.provider import armable
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=config_dir())
    if not any(event.matches(t) for t in armable(store)):
        return
    from personalclaw.triggers.dispatch import Envelope, spool_fire

    payload: dict[str, Any] = {"key": event.key, "value": event.value}
    if event.meta:
        payload["meta"] = dict(event.meta)
    if not spool_fire(
        Envelope(
            seq=0,
            source="event-bus",
            kind=f"{event.source}.{event.event_type}",
            payload=payload,
            emitted_at=event.now,
        )
    ):
        logger.warning(
            "could not spool a %s.%s event a trigger is waiting for; it will not fire",
            event.source,
            event.event_type,
        )
