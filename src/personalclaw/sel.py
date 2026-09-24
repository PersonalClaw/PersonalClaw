"""Security Event Log — immutable, tamper-evident audit trail for tool invocations.

Records structured JSON events for every tool/MCP action with:
- Timestamp (ISO 8601 UTC)
- Caller identity (session key, agent, source interface)
- Caller subsystem (``caller_scope``: which unattended pass invoked the call, read from
  the one shared attribution seam in ``guardrails.audit`` — see :class:`SecurityEvent`)
- Operation type (tool_call, tool_approved, tool_rejected, tool_denied, mcp_call)
- Resources affected (tool name, tool kind, arguments summary)
- Outcome (approved, rejected, denied, completed, failed)
- Downstream service (MCP server name if applicable)
- HMAC-SHA256 integrity chain (each entry signs over previous hash)

Storage: ``~/.personalclaw/security_events.jsonl`` (append-only JSONL)
Retention: configurable, default 365 days.
"""

import hashlib
import hmac
import json
import logging
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)


def _default_dir() -> Path:
    """Resolve the SEL log directory at instantiation time.

    Honors ``PERSONALCLAW_HOME`` so containerized deployments writing to
    ``/data`` see their logs persisted to the mounted volume rather than
    the entrypoint-seeded ``/home/personalclaw/.personalclaw`` directory.
    """
    override = os.environ.get("PERSONALCLAW_HOME")
    if override:
        p = Path(override).expanduser().resolve()
        if p != Path("/") and p.parts[:2] not in (("/", "usr"), ("/", "System"), ("/", "etc")):
            return p
    return Path.home() / ".personalclaw"


_SEL_FILE = "security_events.jsonl"
_RETENTION_DAYS = 365
_HMAC_KEY_FILE = "sel_hmac.key"
_MAX_ARG_LEN = 500
# Default tamper-check window: verify the most recent N entries instead of the
# whole (unbounded, append-only) chain, so the audit UI stays responsive.
_VERIFY_WINDOW = 5000
# Hard size cap for the on-disk log. The chain is append-only and high-rate, so a
# size bound (not just age) keeps reads/verify fast. Comfortably above the verify
# window so a prune never erases the whole verifiable tail.
_MAX_ENTRIES = 50000
#: How many log lines ONE :meth:`SecurityEventLog.audit_page` request may read.
#:
#: A BUDGET, not a wall — and the distinction is the whole of issue #593's second half.
#: This used to be ``scan_cap=_MAX_ENTRIES``: every page re-read the newest 50,000 lines and
#: nothing older was reachable at any page depth. Measured on a 63,653-entry log: 13,653 rows
#: (21.4%) unreachable, and 2.1s of server work for EVERY page including the first, because
#: the whole 50,000-line tail was read and split before the first row was chosen.
#:
#: Now the cursor carries a byte anchor, so a page reads only backward from where the previous
#: one stopped, and hitting this budget hands back an anchor instead of ending the walk. The
#: log is reachable end to end; the bound is on one request's work.
_AUDIT_PAGE_SCAN_BUDGET = 5000

#: The fields the audit read surface may filter on. A CLOSED set: the handler refuses an
#: unknown filter key instead of ignoring it, because a silently-dropped filter returns
#: MORE than the operator asked for while looking like it worked — the fail-open shape an
#: audit surface can least afford.
AUDIT_FILTER_FIELDS = ("caller_identity", "operation", "outcome", "downstream_service")


class AuditOutcomeFamily(TypedDict):
    """One outcome family: what it is called, how it reads, and which words it claims.

    Typed rather than a bare ``dict[str, object]`` so ``tone`` is not optional by accident and so
    consumers stop casting. The casts were the tell: the tone lived in the dashboard because a
    loosely-typed table here made it awkward to carry, and the dashboard's copy then drifted.
    """

    key: str
    label: str
    tone: str
    values: tuple[str, ...]


#: The audit surface's outcome filters, defined HERE because this module owns the log the
#: words are written into. The dashboard used to carry its own two-entry list — one literal
#: substring each, `denied` and `failed` — against a vocabulary of fourteen. Measured across
#: the writers in ``src/personalclaw``:
#:
#:     denied 163 · rejected 24 · blocked 5 · refused 1        the "Denied" pill saw 163 of 193
#:     failure 23 · error 21 · failed 4                        the "Failed" pill saw 4 of 48
#:
#: and confirmed live: a real ``DELETE /api/terminal/sessions/…`` recorded ``outcome=error``
#: was invisible to the "Failed" filter (``outcome=failed`` → 0 rows, ``outcome=error`` → 1).
#: On an audit surface a filter that quietly omits matching records is the worst shape there
#: is — the operator concludes nothing happened. A view over a vocabulary has to be defined
#: WITH the vocabulary, or it describes only the words that existed when someone typed it.
#:
#: ``values`` are matched any-of (see :func:`_audit_matches`), so a family stays one
#: server-side query and pagination keeps agreeing with the pill. Matching is per
#: :func:`_outcome_token_match` — the value, or the value as a whole ``_``-delimited token of a
#: longer word — which is what makes the prefixed variants fall in for free:
#: ``denied_running``/``denied_mismatch``/``denied_invalid`` under ``denied``,
#: ``rejected_spawn``/``rejected_invalid_cwd`` under ``rejected``, the five ``refused_*`` under
#: ``refused``, ``hook_blocked`` under ``blocked``, ``hook_error`` under ``error``. And every
#: value must be a word a writer actually emits — a filter offering a term nobody writes is the
#: same silent-zero defect from the other direction, so ``test_audit_outcome_families.py``
#: checks each one against the tree's real vocabulary.
#:
#: ``tone`` is how a row of this family READS, and it lives here for the same reason the values
#: do. The dashboard kept its own fourteen-entry ``outcome -> colour`` map, and it had drifted
#: from this table: ``not_found`` is in the ``failed`` family and had no entry, so a failure
#: rendered in neutral grey. A hand-maintained view over a vocabulary describes only the words
#: that existed when someone typed it. The server now stamps each row's tone with the matcher
#: below, and the panel maps tone -> design token and holds no outcome vocabulary at all.
AUDIT_OUTCOME_FAMILIES: tuple[AuditOutcomeFamily, ...] = (
    {
        # `declined` joins its siblings here (#3443). It is the losing arm of
        # `outcome="executed" if executed else "declined"` (handlers/proactive.py) — the SAME
        # statement whose winning arm `executed` is classified below, with a written note
        # about its other sibling `expired`. So the decision had been reasoned about and this
        # arm still rendered as nothing, for one reason: the ceiling's scanner read only
        # `outcome="LITERAL"`, so it never showed anyone the word. `rejected` is already here
        # and is the same act (an approval a human refused), which is what settles the tone.
        "key": "denied",
        "label": "Denied",
        "tone": "danger",
        "values": ("denied", "rejected", "blocked", "refused", "declined"),
    },
    {
        # ``not_found`` is included deliberately: the operation did not do what was asked, and
        # an operator scanning for what went wrong wants it. ``tampered`` is NOT — an integrity
        # break is not an operation failure and deserves its own surface, not a bucket.
        "key": "failed",
        "label": "Failed",
        "tone": "danger",
        "values": ("failure", "failed", "error", "not_found"),
    },
    {
        # The control stopped and asked. Not a refusal (nothing was denied) and not a fault
        # (nothing broke), so it gets its own pill rather than being forced into one of the two
        # above — and it is the one tone the panel's old local map carried that no family did.
        #
        # `needs_human` joins them (#3443) and it is the plainest case in the table: the
        # sentence above IS its definition. `guardrails/denylist.py` writes
        # `outcome="blocked" if decision.verdict == "block" else "needs_human"` — one decision,
        # two arms, and only the arm spelled as a bare literal was ever visible to the rail, so
        # a denylist verdict that stopped to ask a human rendered neutral beside a `blocked`
        # that rendered danger.
        "key": "needs_confirm",
        "label": "Needs confirmation",
        "tone": "warning",
        "values": ("needs_confirm", "needs_input", "needs_human"),
    },
    {
        # 🔴 THE PILLS COVERED 0.6% OF THE LOG (issue #535). Measured on a live 1,040-entry log:
        # `ok` alone was 1,021 rows (98.2%), and it matched NEITHER pill — so the two filters
        # together reached 6 rows. The vocabulary below was already classified (it is the list
        # the coverage rail uses) but was never OFFERED, so an operator could narrow to what
        # went wrong and never to what happened. A view that cannot select the bulk of the log
        # is not a filter, and on an audit surface "no matching events" reads as "nothing
        # happened".
        #
        # This family is why matching is token-bounded and not substring. Measured against the
        # tree's 66 emitted outcome words, a substring `ok` also captures `hook_blocked` and
        # `hook_error` — both of which belong to the families above — and `approved` captures
        # `not_auto_approved`, its own negation. A "Succeeded" pill returning a blocked hook is
        # the same silent lie as a "Failed" pill that misses one.
        "key": "ok",
        "label": "Succeeded",
        "tone": "success",
        "values": (
            "success",
            "ok",
            # PA-5. Both emitters spell it as the SUCCESS arm of an explicit pair —
            # `outcome="executed" if executed else "declined"` (handlers/proactive.py) and
            # `"outcome": "executed" if ok else "failed"` (proactive/autoexec.py) — so a triage
            # reply's verb having been carried out is a working operation, not something a
            # failure pill should accuse. Its sibling `expired` is deliberately left
            # UNCLASSIFIED: nobody answered, which is not a refusal, and putting it in a denied
            # family would make the audit log assert a refusal that never happened. PA-5's own
            # note is the distinction — "you answered and it did nothing" must stay legible
            # against "you never answered".
            "executed",
            "allowed",
            "approved",
            "auto_approved",
            "completed",
            "granted",
            "enabled",
            "disabled",
        ),
    },
)

#: The success vocabulary, DERIVED from the family above rather than kept beside it. It was a
#: second tuple, and a second copy of a vocabulary is how the two drift; consumers
#: (``browse/grant.py``, the coverage rail) want the words, and there is now one place holding
#: them.
AUDIT_OUTCOME_SUCCESS: tuple[str, ...] = next(
    f["values"] for f in AUDIT_OUTCOME_FAMILIES if f["key"] == "ok"
)

#: The tones a row may be stamped with. A CLOSED set: the frontend maps each to a design token,
#: so a family declaring a tone nobody renders would silently fall back to neutral — the
#: hand-maintained-list failure this whole table exists to end. Checked at import.
AUDIT_OUTCOME_TONES = ("danger", "warning", "success", "neutral")

_DECLARED_TONES = {f["tone"] for f in AUDIT_OUTCOME_FAMILIES}
if not _DECLARED_TONES <= set(AUDIT_OUTCOME_TONES):
    raise RuntimeError(
        "an outcome family declares a tone the renderers do not know: "
        f"{sorted(_DECLARED_TONES - set(AUDIT_OUTCOME_TONES))}"
    )

#: Structural fields :func:`redact_event` leaves byte-identical. Each is machine-generated
#: and cannot carry a user/tool payload, so there is nothing in them to redact — while
#: rewriting them would be actively harmful: mangling ``entry_hash``/``prev_hash`` makes an
#: exported record unverifiable by anyone holding the key, which is the whole point of a
#: tamper-evident log. (``_B64_CHUNK_RE`` in the redactor matches any 40+ char run of the
#: base64 alphabet, and a 64-char hex digest qualifies; it only rewrites when the decoded
#: bytes look like a credential, so today it spares them by luck. This makes it by rule.)
_UNREDACTED_FIELDS = frozenset({"event_id", "timestamp", "event_type", "prev_hash", "entry_hash"})


@dataclass
class SecurityEvent:
    """A single auditable security event."""

    event_id: str
    timestamp: str  # ISO 8601 UTC
    event_type: str  # tool_invocation, tool_approval, tool_denial, mcp_call, api_access
    caller_identity: str  # session key or user identifier
    agent: str  # agent name (personalclaw, custom, etc.)
    source: str  # channel, dashboard, cli, cron, subagent, background
    operation: str  # tool name or API operation
    tool_kind: str = ""  # execute_bash, fs_write, mcp, etc.
    outcome: str = ""  # approved, rejected, denied, completed, failed
    resources: str = ""  # affected resources summary (truncated)
    downstream_service: str = ""  # MCP server name if applicable
    request_id: str = ""  # ACP permission request ID
    error: str = ""
    #: WHICH SUBSYSTEM's pass was running when this event was recorded — one of
    #: :data:`personalclaw.guardrails.audit.CALLERS`, or "" when nothing bound one (`G47`,
    #: ACP-AGENT-PARITY). ``caller_identity`` above answers "whose SESSION" (a security
    #: ACTOR — a session key / ``user`` / a remote address); this answers "whose PASS", the
    #: per-call caller the actor alone cannot carry. Filled by :meth:`SecurityEventLog.log`
    #: from the SAME ``guardrails.audit.caller_scope`` seam that stamps the model-call
    #: ledger's ``caller`` column, so the two surfaces name a caller ONE way, not two.
    #: 🔴 WHY IT EXISTS. Before this, a SEL row written inside ``caller_scope("skill_ladder")``
    #: was byte-for-byte identical (bar its unique id/timestamp/hash) to one written outside
    #: it, so a ladder pass that RAN and declined and one that NEVER FIRED were the same
    #: observation from the log — the G47 instrumentation gap the ACP parity audit was blocked
    #: on. Named ``caller_scope`` (not ``caller``) deliberately: SEL already overloads
    #: ``caller``/``caller_identity`` for the actor, so a second ``caller`` here would collide.
    caller_scope: str = ""
    prev_hash: str = ""  # HMAC chain — hash of previous entry
    entry_hash: str = ""  # HMAC of this entry (computed on write)
    metadata: dict = field(default_factory=dict)


def redact_event(record: dict) -> dict:
    """Deep-redact one SEL record for any consumer OUTSIDE this process.

    The log stores a truncated summary of real tool arguments, so a record can carry a
    secret a user pasted into a command. Every path that hands a record to something
    other than the on-disk log goes through here — the forward callback and the audit
    read surface — so there is exactly ONE answer to "what does a SEL record look like
    once it leaves". A second copy of this walk is how the export and the table would
    end up disagreeing about which fields are safe.

    Structural fields (:data:`_UNREDACTED_FIELDS`) pass through untouched; everything
    else, at any nesting depth, goes through :func:`personalclaw.security.redact`. The
    input is not mutated.
    """
    from personalclaw.security import redact

    def _deep(obj: object) -> object:
        if isinstance(obj, str):
            return redact(obj)
        if isinstance(obj, dict):
            return {k: (v if k in _UNREDACTED_FIELDS else _deep(v)) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return type(obj)(_deep(i) for i in obj)
        return obj

    return {k: (v if k in _UNREDACTED_FIELDS else _deep(v)) for k, v in record.items()}


def _outcome_token_match(value: str, word: str) -> bool:
    """Whether ``word`` belongs to the outcome ``value``, matched on ``_`` token boundaries.

    ``outcome`` is the one filterable field whose vocabulary WE write (:data:`SecurityEvent`),
    so it gets a matcher that understands the shape of those words instead of the plain
    substring the free-text fields use. A value matches a word it equals, or of which it is a
    whole underscore-delimited token: ``error`` matches ``hook_error``, ``denied`` matches
    ``denied_running``, ``refused`` matches ``refused_low_memory``.

    The two things it deliberately does NOT match, both measured against the tree's 66 emitted
    outcome words and both of which a substring match got wrong:

    * ``ok`` does not match ``hook_blocked``/``hook_error``/``invoked``. Under substring, the
      ``ok`` success family captured a blocked hook and a hook error — rows that belong to the
      denied and failed families — so a "Succeeded" pill would have asserted success for both.
    * a positive value does not match a ``not_``-prefixed word: ``approved`` does not match
      ``not_auto_approved``. Capturing a negation as its own affirmative is the same lie in a
      subtler form. ``not_found`` still matches itself, because the value carries the prefix.
    """
    if not value.startswith("not_") and word.startswith("not_"):
        return False
    return (
        word == value
        or word.startswith(f"{value}_")
        or word.endswith(f"_{value}")
        or f"_{value}_" in word
    )


def audit_outcome_tone(outcome: str) -> str:
    """The tone one outcome word READS as: one of :data:`AUDIT_OUTCOME_TONES`.

    Stamped onto every row by :meth:`SecurityEventLog.audit_page` so the tone and the filter
    pills come from the SAME table and the same matcher. The dashboard used to hold its own
    ``outcome -> colour`` map; it was missing ``not_found`` (a member of the ``failed`` family)
    and five success words, so those rows rendered neutral while the pill called them failures.

    An unclassified word is ``"neutral"`` on purpose. Guessing a tone for a word nobody
    classified is how the audit log would come to assert a verdict no one decided.
    """
    word = outcome.strip().lower()
    if not word:
        return "neutral"
    for family in AUDIT_OUTCOME_FAMILIES:
        if any(_outcome_token_match(v, word) for v in family["values"]):
            return family["tone"]
    return "neutral"


def _audit_cursor(offset: int, record: dict) -> str:
    """Encode a resumable audit anchor: ``"<byte offset>.<event_id>"``.

    Both halves are load-bearing. The offset is what makes the next page an O(page) read
    instead of a re-scan from the tail; the ``event_id`` is what lets
    :meth:`SecurityEventLog._resolve_audit_cursor` prove the offset still points at the record
    the client was given, so a cursor stale from a ``prune()`` fails closed instead of quietly
    resuming somewhere else in the log.
    """
    return f"{offset}.{record.get('event_id', '')}"


def _audit_matches(data: dict, filters: dict[str, str], since: str, until: str) -> bool:
    """Whether one record satisfies every active filter (AND across fields).

    Free-text fields (``caller_identity``/``operation``/``downstream_service``) are
    case-insensitive SUBSTRING matches — you type ``terminal`` and it finds
    ``DELETE /api/terminal/sessions/x``. ``outcome`` is matched on token boundaries instead
    (:func:`_outcome_token_match`), because it is a closed vocabulary this module writes and a
    substring over it mis-classifies real words: ``ok`` inside ``hook_error``.

    The time bounds are lexicographic over the ISO-8601 UTC timestamp, which is
    ordering-correct because every writer formats it identically
    (``datetime.now(tz=utc).isoformat()``).

    A comma in a needle means ANY-OF: ``outcome=failure,error,failed`` matches a record
    whose outcome is any one of them. That is what lets an outcome FAMILY
    (:data:`AUDIT_OUTCOME_FAMILIES`) stay a single server-side query, so the filter pill and
    the pagination cursor cannot disagree — the reason these filters became server-side in
    the first place. AND still holds ACROSS fields; the OR is only within one field.
    """
    for field_name, needle in filters.items():
        haystack = str(data.get(field_name, "")).lower()
        alternatives = [part.strip().lower() for part in needle.split(",") if part.strip()]
        if not alternatives:
            continue
        if field_name == "outcome":
            if not any(_outcome_token_match(alt, haystack) for alt in alternatives):
                return False
        elif not any(alt in haystack for alt in alternatives):
            return False
    ts = str(data.get("timestamp", ""))
    if since and ts < since:
        return False
    if until and ts > until:
        return False
    return True


class SecurityEventLog:
    """Append-only, HMAC-chained security event log.

    Thread-safe. Singleton pattern — all callers share one instance.
    """

    _instance: "SecurityEventLog | None" = None
    _init_lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls, base_dir: Path | None = None) -> "SecurityEventLog":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._initialized = False
                    cls._instance = inst
        return cls._instance

    def __init__(self, base_dir: Path | None = None) -> None:
        if self._initialized:
            return
        self._dir = base_dir or _default_dir()
        self._path = self._dir / _SEL_FILE
        self._lock = threading.Lock()
        self._hmac_key = self._load_or_create_hmac_key()
        self._last_hash = self._read_last_hash()
        self._forward_callback: Callable[[dict], None] | None = None
        self._initialized = True

    def set_forward_callback(self, callback: Callable[[dict], None] | None) -> None:
        """Register an optional callback to forward events to a centralized log system."""
        with self._lock:
            self._forward_callback = callback

    def _load_or_create_hmac_key(self) -> bytes:
        key_path = self._dir / _HMAC_KEY_FILE
        self._dir.mkdir(parents=True, exist_ok=True)
        if key_path.exists():
            return key_path.read_bytes()
        key = os.urandom(32)
        key_path.write_bytes(key)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        return key

    def _size(self) -> int:
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    def _lines_backward(self, end: int) -> "Iterator[tuple[int, str]]":
        """Yield ``(start_offset, text)`` for every non-empty line strictly before byte ``end``,
        NEWEST FIRST, reading the file backward in chunks.

        THE one backward reader. There used to be three hand-rolled reverse chunk scans in this
        class — ``_read_last_hash``, ``_tail_lines`` and (through the latter) ``audit_page`` —
        and only the last of them needed a line's POSITION, which is why it could not have one:
        the other two threw the offsets away, so pagination had nothing to anchor on and every
        page restarted from the tail. Yielding the offset costs nothing and is what makes an
        audit cursor resumable (see :meth:`audit_page`).

        The offset is the byte at which the line's first character sits, so passing it back as
        ``end`` resumes strictly older than that line — the line itself is never re-served.
        """
        if end <= 0 or not self._path.exists():
            return
        try:
            with open(self._path, "rb") as f:
                pos = end
                # Bytes of a line whose start lies before `pos`; resolved by the next chunk.
                carry = b""
                while pos > 0:
                    step = min(65536, pos)
                    pos -= step
                    f.seek(pos)
                    buf = f.read(step) + carry
                    parts = buf.split(b"\n")
                    carry = parts[0]
                    offset = pos + len(parts[0]) + 1
                    complete: list[tuple[int, bytes]] = []
                    for part in parts[1:]:
                        complete.append((offset, part))
                        offset += len(part) + 1
                    for line_offset, part in reversed(complete):
                        if part.strip():
                            yield line_offset, part.decode("utf-8", "replace")
                if carry.strip():
                    yield 0, carry.decode("utf-8", "replace")
        except OSError:
            return

    def _read_last_hash(self) -> str:
        try:
            for _offset, line in self._lines_backward(self._size()):
                return str(json.loads(line).get("entry_hash", ""))
        except Exception:
            return ""
        return ""

    def _tail_lines(self, max_lines: int) -> list[str]:
        """Return up to ``max_lines`` trailing non-empty lines, reading only the
        end of the file. The SEL log is append-only and grows without bound (every
        gateway/channel/mcp action appends), so reads MUST stay O(tail) — never load
        the whole file just to show recent events or sample-verify the chain.
        """
        out: list[str] = []
        for _offset, line in self._lines_backward(self._size()):
            out.append(line)
            if len(out) >= max_lines:
                break
        out.reverse()
        return out

    def _record_is_authentic(self, data: dict) -> bool:
        """Whether one parsed record's stored HMAC matches its recomputed digest.

        ONE definition of "is this record authentic", shared by :meth:`verify_integrity`
        (the aggregate the banner shows) and :meth:`audit_page` (the per-row badge). Two
        copies would let the summary and the row disagree about the same entry, and the
        operator would have no way to tell which one lied.
        """
        stored = data.get("entry_hash", "")
        payload = {k: v for k, v in data.items() if k != "entry_hash"}
        expected = hmac.new(
            self._hmac_key, json.dumps(payload, sort_keys=True).encode(), hashlib.sha256
        ).hexdigest()
        return bool(stored) and hmac.compare_digest(str(stored), expected)

    def _compute_hash(self, event: SecurityEvent) -> str:
        # Hash over all fields except entry_hash itself
        d = asdict(event)
        d.pop("entry_hash", None)
        payload = json.dumps(d, sort_keys=True).encode()
        return hmac.new(self._hmac_key, payload, hashlib.sha256).hexdigest()

    def log(self, event: SecurityEvent) -> None:
        """Append an event to the log with HMAC chain integrity."""
        # `G47`: stamp the subsystem whose pass is running when this event is recorded,
        # read from the ONE shared attribution seam (`guardrails.audit.caller_scope`) that
        # also stamps the model-call ledger's `caller` column. This is what lets an audit
        # tell "a ladder ran and declined" from "a ladder never fired": two otherwise
        # identical calls from the same session now differ by who invoked them. An explicit
        # value on the event wins (only an unset field is filled), and reading a ContextVar
        # changes nothing about the call itself — so a call that already ran is unaffected.
        # Set before `_compute_hash` so the field is inside the tamper-evident HMAC.
        if not event.caller_scope:
            from personalclaw.guardrails.audit import current_caller

            event.caller_scope = current_caller()
        with self._lock:
            event.prev_hash = self._last_hash
            event.entry_hash = self._compute_hash(event)
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(event)) + "\n")
            self._last_hash = event.entry_hash
            callback = self._forward_callback
        if callback:
            try:
                callback(redact_event(asdict(event)))
            except Exception:
                logger.warning("forward_callback failed", exc_info=True)

    def log_tool_invocation(
        self,
        *,
        session_key: str,
        agent: str = "personalclaw",
        source: str = "",
        tool_name: str,
        tool_kind: str = "",
        outcome: str,
        request_id: str | int = "",
        downstream_service: str = "",
        resources: str = "",
        error: str = "",
        metadata: dict | None = None,
    ) -> None:
        """Convenience: log a tool invocation event."""
        self.log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type="tool_invocation",
                caller_identity=session_key,
                agent=agent,
                source=source or _infer_source(session_key),
                operation=tool_name,
                tool_kind=tool_kind,
                outcome=outcome,
                request_id=str(request_id),
                downstream_service=downstream_service,
                resources=resources[:_MAX_ARG_LEN] if resources else "",
                error=error[:_MAX_ARG_LEN] if error else "",
                metadata=metadata or {},
            )
        )

    def log_api_access(
        self,
        *,
        caller: str,
        operation: str,
        outcome: str,
        source: str = "dashboard",
        resources: str = "",
        error: str = "",
        metadata: dict | None = None,
    ) -> None:
        """Convenience: log a dashboard/API access event.

        ``error`` is for why an access FAILED; it has no meaning on a successful
        outcome and a reader (``personalclaw security events``) treats any non-empty
        value as evidence something went wrong. A caller that wants to record WHY a
        successful access was allowed (e.g. "local-network bypass") belongs in
        ``metadata`` instead — the same field :meth:`log_tool_invocation` callers
        already use for this (see ``subagent.py``'s ``metadata={"reason": ...}``
        convention) — never in ``error``.
        """
        self.log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type="api_access",
                caller_identity=caller,
                agent="",
                source=source,
                operation=operation,
                outcome=outcome,
                resources=resources[:_MAX_ARG_LEN] if resources else "",
                error=error[:_MAX_ARG_LEN] if error else "",
                metadata=metadata or {},
            )
        )

    def verify_integrity(self, max_entries: int | None = _VERIFY_WINDOW) -> tuple[int, int]:
        """Verify the per-entry HMAC chain. Returns (checked_entries, valid_entries).

        An entry is counted as valid when its standalone HMAC matches the
        recomputed payload digest. We tolerate `prev_hash` mismatches (chain
        breaks) silently because PersonalClaw's gateway, channel, and mcp
        processes each write to the same log without IPC; their writes
        interleave and the per-process ``_last_hash`` doesn't survive
        cross-process ordering. The HMAC over each individual record is still
        verifiable so a single tampered record stands out clearly.

        The SEL log is append-only and unbounded — every gateway/channel/mcp action
        appends an entry — so a full walk is O(n) and grows without limit (it had
        reached >1M entries, taking 20s+ and hanging the audit UI). By default we
        verify only the most recent ``max_entries`` (the live-tamper-detection
        window); pass ``max_entries=None`` for an exhaustive offline check.
        """
        if not self._path.exists():
            return 0, 0
        if max_entries is None:
            lines: list[str] = [
                ln.strip()
                for ln in self._path.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
        else:
            lines = self._tail_lines(max_entries)
        checked = 0
        valid = 0
        for line in lines:
            checked += 1
            try:
                if self._record_is_authentic(json.loads(line)):
                    valid += 1
                else:
                    logger.warning("SEL HMAC mismatch at entry %d", checked)
            except (json.JSONDecodeError, Exception):
                logger.warning("SEL parse error at entry %d", checked)
        return checked, valid

    def rotate(self, archive: bool = True) -> dict:
        """Rotate the SEL log to start a fresh HMAC chain.

        When ``archive`` is True (default) the existing log is renamed with a
        timestamp suffix; otherwise it is deleted. Use this to clear a chain
        break and start a clean HMAC chain. Returns a dict with the
        before/after entry count and the archive path (if any).
        """
        from datetime import datetime, timezone

        with self._lock:
            entries_before = 0
            archive_path: Path | None = None
            if self._path.exists():
                try:
                    entries_before = sum(
                        1
                        for line in self._path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    )
                except OSError:
                    entries_before = 0
                if archive:
                    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    archive_path = self._path.with_name(
                        f"{self._path.stem}.{ts}.bak{self._path.suffix}"
                    )
                    try:
                        self._path.rename(archive_path)
                    except OSError:
                        archive_path = None
                        self._path.unlink(missing_ok=True)
                else:
                    self._path.unlink(missing_ok=True)
            self._last_hash = ""
        return {
            "rotated": True,
            "entries_before": entries_before,
            "entries_after": 0,
            "archive_path": str(archive_path) if archive_path else "",
        }

    def recent(self, limit: int = 100) -> list[dict]:
        """Return the most recent events (newest first), reading only the file tail."""
        result: list[dict] = []
        for line in reversed(self._tail_lines(limit)):
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(result) >= limit:
                break
        return result

    def _resolve_audit_cursor(self, cursor: str) -> int | None:
        """Byte offset an audit cursor anchors at, or ``None`` if it no longer holds.

        The token is ``"<offset>.<event_id>"``. Both halves are checked, and a failure of
        either is a refusal rather than a fallback: the offset must still be the START of a
        line, and the record there must still be the one the cursor names. A ``prune()`` or
        ``rotate()`` rewrites the file, so a stale offset lands mid-record or on a different
        one — exactly the case that must fail CLOSED, because restarting from the newest
        record would re-serve the whole trail as if it were a fresh page.
        """
        raw_offset, _, event_id = cursor.partition(".")
        if not _:  # no separator — a bare id (the pre-#593 token) or junk
            return None
        try:
            offset = int(raw_offset)
        except ValueError:
            return None
        if offset < 0 or offset > self._size():
            return None
        try:
            with open(self._path, "rb") as f:
                if offset:
                    # A line START, or the cursor is pointing into the middle of a record.
                    f.seek(offset - 1)
                    if f.read(1) != b"\n":
                        return None
                else:
                    f.seek(0)
                line = f.readline()
        except OSError:
            return None
        if not line.strip():
            return None
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict) or str(data.get("event_id", "")) != event_id:
            return None
        return offset

    def audit_page(
        self,
        *,
        limit: int = 50,
        cursor: str = "",
        filters: dict[str, str] | None = None,
        since: str = "",
        until: str = "",
        scan_budget: int | None = None,
    ) -> dict:
        """Return one filtered page of audit records, newest first, redacted.

        **Why a cursor and not an offset.** The log is append-only and this surface reads
        it newest-first, so a row ``offset`` is unstable by construction: append *k* entries
        between page 1 and page 2 and every element shifts *k* places toward the tail, so
        page 2 re-serves *k* rows the operator already saw — and a concurrent ``prune()``
        shifts the other way and SKIPS rows. Skipping rows in an audit trail is the
        failure that matters: the surface would omit events while looking complete.

        ``cursor`` is an opaque ``"<byte offset>.<event_id>"`` anchor on the last line the
        previous page looked at. A page is the next ``limit`` matching records strictly OLDER
        than that anchor. Appends land strictly newer than it, so they cannot enter or shift
        any page taken after it — pages 2..N are stable under concurrent writes, while page 1
        (no cursor) still shows the true live tail. An anchor that no longer holds returns
        ``cursor_found=False`` and the caller REFUSES.

        **The budget is not a wall (issue #593).** The anchor carries the byte position, so a
        page reads backward only from where the previous one stopped instead of re-reading the
        tail. ``scan_budget`` bounds ONE request's work; when it stops the walk early the page
        comes back with an anchor at the stopping point and ``truncated=True``, so the operator
        continues from there. This used to be ``scan_cap=_MAX_ENTRIES``, bounding the whole
        LOG: measured on a 63,653-entry log, 13,653 rows (21.4%) were unreachable at any page
        depth, and every page — including the first — cost 2.1s because the newest 50,000 lines
        were read and split before the first row was chosen.

        Per-record ``integrity_ok`` is computed on the RAW line, before redaction — redacting
        first would rewrite the payload the HMAC covers and report every record as tampered.
        ``outcome_tone`` comes from the same table the filter pills do
        (:func:`audit_outcome_tone`), so a row cannot read green while a pill calls it a
        failure.
        """
        # Resolved at CALL time, not bound as a default: a default argument freezes the module
        # constant at import, which silently made the reachability rails below pass without the
        # budget ever biting. A bound already read by nobody is not a bound.
        budget = _AUDIT_PAGE_SCAN_BUDGET if scan_budget is None else scan_budget
        empty = {
            "events": [],
            "next_cursor": "",
            "scanned": 0,
            "truncated": False,
            "cursor_found": True,
        }
        if not self._path.exists():
            return empty
        end = self._size()
        if cursor:
            resolved = self._resolve_audit_cursor(cursor)
            if resolved is None:
                return {**empty, "cursor_found": False}
            end = resolved

        active = {k: v for k, v in (filters or {}).items() if v}
        page: list[tuple[int, dict]] = []
        next_cursor = ""
        truncated = False
        scanned = 0
        for offset, line in self._lines_backward(end):
            scanned += 1
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and _audit_matches(data, active, since, until):
                if len(page) >= limit:
                    # One match BEYOND the page proves a next page exists, so a cursor is
                    # only ever handed out when it leads somewhere.
                    next_cursor = _audit_cursor(*page[-1])
                    break
                authentic = self._record_is_authentic(data)
                row = redact_event(data)
                row["integrity_ok"] = authentic
                row["outcome_tone"] = audit_outcome_tone(str(data.get("outcome", "")))
                page.append((offset, row))
            if scanned >= budget and offset > 0:
                # Out of budget with older bytes still on disk. Hand back an anchor here —
                # a bounded read that ENDS the walk is what made 21% of the log unreachable.
                truncated = True
                next_cursor = _audit_cursor(
                    offset, data if isinstance(data, dict) else {"event_id": ""}
                )
                break
        return {
            "events": [row for _offset, row in page],
            "next_cursor": next_cursor,
            "scanned": scanned,
            # This request stopped on its scan budget, not on the start of the log. Always
            # paired with a usable ``next_cursor``: reported so a consumer can say "still
            # looking" rather than "that is everything".
            "truncated": truncated,
            "cursor_found": True,
        }

    def _prune_plan(self, keep_days: int, max_entries: int) -> tuple[list[str], int]:
        """Compute ``(kept_lines, removed_count)`` without writing anything. Shared by
        ``prune`` and ``count_prunable`` so the measured deficit is exactly the number of
        entries a prune would drop."""
        if not self._path.exists():
            return [], 0
        from datetime import timedelta

        cutoff_str = (datetime.now(tz=timezone.utc) - timedelta(days=keep_days)).isoformat()

        lines = self._path.read_text(encoding="utf-8").splitlines()
        kept: list[str] = []
        removed = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("timestamp", "") < cutoff_str:
                    removed += 1
                    continue
            except json.JSONDecodeError:
                removed += 1
                continue
            kept.append(line)

        # Size cap: keep only the newest max_entries (entries are appended in order).
        if max_entries > 0 and len(kept) > max_entries:
            removed += len(kept) - max_entries
            kept = kept[-max_entries:]
        return kept, removed

    def count_prunable(
        self, keep_days: int = _RETENTION_DAYS, max_entries: int = _MAX_ENTRIES
    ) -> int:
        """Read-only count of entries a ``prune()`` would remove (the remediation engine's
        measured deficit for the SEL prune)."""
        return self._prune_plan(keep_days, max_entries)[1]

    def prune(self, keep_days: int = _RETENTION_DAYS, max_entries: int = _MAX_ENTRIES) -> int:
        """Trim the log. Returns the number of entries removed.

        Two bounds, both applied (whichever drops more wins per entry):
        - age: drop entries older than ``keep_days``.
        - size: keep at most the newest ``max_entries``.

        The size cap is the real defense — the log is append-only and high-rate
        (every gateway/channel/mcp action, including dashboard polls, appends), so an
        age-only prune still lets the file grow to millions of entries within the
        retention window and makes reads/verify crawl. Pass ``max_entries<=0`` to
        disable the size cap.
        """
        kept, removed = self._prune_plan(keep_days, max_entries)
        if removed:
            with self._lock:
                atomic_write(self._path, "\n".join(kept) + "\n" if kept else "")
                self._last_hash = self._read_last_hash()
            logger.info(
                "SEL pruned %d entries (keep_days=%d, max_entries=%d)",
                removed,
                keep_days,
                max_entries,
            )
        return removed


def _infer_source(session_key: str) -> str:
    """Infer the source interface from a session key."""
    # A run-owned stage session (WORK-CONTAINERS §5.1, S50). Registered HERE because this is the
    # function `log_tool_call` actually calls — a helper elsewhere that returned "workflow" would
    # have been a parallel path the audit log never consults, leaving every run-owned tool call
    # recorded as `channel`, the catch-all where unrecognized keys silently land. That makes "what
    # did the run do" unanswerable from the log even though every event is in it.
    if session_key.startswith("workflow:"):
        return "workflow"
    if session_key.startswith("dashboard:"):
        return "dashboard"
    if session_key.startswith("cron:"):
        return "cron"
    if session_key.startswith("subagent:"):
        return "subagent"
    if session_key == "_bg":
        return "background"
    if session_key == "cli_chat":
        return "cli"
    return "channel"


def sel() -> SecurityEventLog:
    """Module-level accessor for the singleton SEL instance."""
    return SecurityEventLog()
