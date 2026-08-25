"""Attempt-level JSONL audit trail for the model-call chokepoint (§2.1).

One line per ATTEMPT (not per request) in ``~/.personalclaw/model_calls.jsonl`` —
so a request that retried once and then fell back writes three lines sharing one
``audit_id``. This is harness mechanics (a file under the config dir), NOT a
memory entry or knowledge item (§7 memory/knowledge boundary): nothing here
writes to ``memory.db`` / ``knowledge.db``.

The file is append-mostly with a trim at 2× the line cap (the ``notifications.jsonl``
pattern the plan cites): each write appends, and when the file crosses ``2 × cap``
lines it is rewritten to the last ``cap`` lines. Trimming at 2× rather than every
write keeps the hot background path append-only in the common case.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.guardrails.failure import FailureMode

logger = logging.getLogger(__name__)

_AUDIT_FILENAME = "model_calls.jsonl"
_LINE_CAP = 5000  # keep the most recent N attempts; trim triggers at 2× this


def _audit_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / _AUDIT_FILENAME


# ── caller attribution: WHICH SUBSYSTEM asked for this call (ACP-AGENT-PARITY G47) ──────

#: The CLOSED caller vocabulary. **This tuple is the one definition of "which subsystem
#: asked for a model call"** — a new unattended subsystem adds its name here and binds it
#: with :func:`caller_scope`; no other spelling is accepted anywhere.
#:
#: 🔴 WHY THE FIELD EXISTS. ``use_case`` says which AXIS a call resolved on
#: (``reasoning|background|loops|orchestration``) and FOUR unrelated subsystems share
#: ``background``, so "a background attempt failed" named no subsystem: an expensive
#: learning pass could be dead in production and no surface would say so. Measured on a
#: skill-ladder pass that died as ``provider_error`` at 60,010 ms (`G47`, ex-`K56`) — the
#: row was recorded, and nothing on it or in the log said which pass it was.
#:
#: 🔴 WHY CLOSED. An open string field answers "who asked" in six spellings within a year;
#: this repo has that scar twice over (four verdict dialects, ``loop`` vs ``loops``).
#: :func:`set_current_caller` REFUSES an unlisted value — the same posture
#: ``routing.policy.set_mode`` takes on an unknown mode, and for the same reason: a write
#: path that silently stores a value its readers will not recognise is worse than a loud one.
#:
#: 🔴 WHY THIS IS THE FIRST SUCH VOCABULARY, NOT A SECOND. The SEL's ``caller`` /
#: ``caller_identity`` (``routing/policy.py``, ``model_call.py``'s own SEL record) is a
#: SECURITY-event ACTOR — "who invoked this API", values like ``user`` / ``system`` / a
#: remote address — it names no subsystem and is not a column of this record.
#: ``usage_ledger``'s ``source`` and ``routing.usage.PURPOSES`` are the TURN ledger's axes,
#: one record removed from an attempt, and ``PURPOSES`` is deliberately coarse: every value
#: below maps to its single ``background`` purpose, which is exactly the collapse `G47`
#: reports. So none of the three could carry this without being redefined.
#: `triage_gate` and `triage_propose` are the triage digest's two background passes
#: (PROACTIVE-ASSISTANT §1.2/§1.3), and they are TWO values rather than one `triage`: the
#: whole point of the gate is that it is the cheap call, so a spend audit that could not tell
#: it apart from the proposal call could not answer whether the gate is earning its keep.
CALLERS: tuple[str, ...] = (
    "conflict_merge",
    "inbox_triage",
    "nl_to_cron",
    "skill_ladder",
    "triage_gate",
    "triage_propose",
)

#: What an attempt with no bound caller reads as on a read surface. Interactive chat is
#: deliberately unguarded (``provider_bridge`` attaches the guard on four axes only), and a
#: subsystem that has not been taught to bind is honestly unattributed rather than guessed.
UNATTRIBUTED = "(unattributed)"

#: The AMBIENT caller every guarded model call is attributed to, when one is bound.
#:
#: A ContextVar for the reason ``budgets._CURRENT_RUN_KEY`` is one: the guard is built by
#: ``provider_bridge`` from provider config alone and has no caller identity, so threading a
#: parameter down would touch all 33 call sites that reach the bridge. One seam sets it, one
#: seam reads it (``model_call.ModelCallGuard._audit``).
_CURRENT_CALLER: contextvars.ContextVar[str] = contextvars.ContextVar(
    "personalclaw_current_model_caller", default=""
)


def set_current_caller(caller: str):
    """Bind the subsystem model calls are attributed to. Returns a token; ``reset()`` it after.

    Raises ``ValueError`` on a value outside :data:`CALLERS`: this is a WRITE path, and a
    write path must reject an unknown value loudly rather than storing something the read
    path cannot recognise (``routing.policy.set_mode``'s contract). ``""`` is allowed and
    means "unattributed" — clearing a binding is not a typo.

    Token-scoped rather than cleared to ``""`` so a nested pass restores its parent's
    attribution instead of losing it, the contract ``budgets.set_current_run_key`` uses.
    """
    if caller and caller not in CALLERS:
        raise ValueError(f"unknown model-call caller {caller!r} (expected one of {CALLERS})")
    return _CURRENT_CALLER.set(caller or "")


def reset_current_caller(token) -> None:
    """Restore the prior caller. NEVER raises — a failed reset must not break a teardown.

    Catches ``Exception`` deliberately: a reused token raises ``RuntimeError``, and this runs
    in a ``finally`` on the model-call path, where anything escaping would replace a real
    provider error with a bookkeeping one (``budgets.reset_current_run_key``'s finding).
    """
    try:
        _CURRENT_CALLER.reset(token)
    except Exception:  # noqa: BLE001 - see the docstring
        _CURRENT_CALLER.set("")


def current_caller() -> str:
    """The bound caller, or ``""`` when a call is not inside an attributed pass."""
    return _CURRENT_CALLER.get() or ""


@contextlib.contextmanager
def caller_scope(caller: str):
    """Attribute every guarded model call inside this block to ``caller``.

    The seam a subsystem uses. Sync (not async) on purpose: a ContextVar set inside a
    coroutine is visible to everything it awaits, so one ``with`` wraps an ``await`` fine —
    while a task created OUTSIDE the block keeps its own copied context, which is the
    correct answer for a pass that forks work it does not own.
    """
    token = set_current_caller(caller)
    try:
        yield
    finally:
        reset_current_caller(token)


@dataclass
class AttemptRecord:
    """One attempt's audit row. Field order is the on-disk column order."""

    audit_id: str
    ts: float
    use_case: str
    provider: str
    model: str
    attempt: int
    failure_mode: str = FailureMode.NONE.value
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    dollars_est: float = 0.0
    estimated: bool = False  # dollars/tokens are heuristic, not provider-reported
    passed: bool = False
    strategy: str = "direct"  # direct | retry | fallback
    degraded: bool = False  # satisfied only by a fallback ref (discount downstream)
    # The routing query class (MODEL-ROUTING-TELEMETRY §2) this attempt served, from the
    # pure classifier — "" when routing/classification didn't run. The stats layer folds
    # per (use_case, query_class), so it's a first-class column, not an ``extra`` field.
    query_class: str = ""
    # Routing provenance (MODEL-ROUTING-TELEMETRY §3.3, MRT-4). ``routed`` = the router reordered
    # the candidate refs for this resolution; ``routed_fallback`` = the routed-FIRST candidate did
    # not serve it and a later ref (typically cloud) did — the "cloud rescue" signal.
    #
    # Deliberately DISTINCT from ``degraded`` above, which they will often co-occur with: degraded
    # answers "did a fallback ref serve this?", routed_fallback answers "did the ROUTER's chosen
    # ordering hold?". Collapsing them would make a router's local-first bet indistinguishable
    # from a user's own chain falling through, and the two need opposite responses — one retunes
    # the policy, the other tells the user a bound provider is down.
    routed: bool = False
    routed_fallback: bool = False
    # WHICH SUBSYSTEM asked for this call — one of :data:`CALLERS`, or "" when nothing bound
    # one (`G47`). A first-class column, not an ``extra`` field, because every read surface
    # over this file wants to group by it: without it four unrelated background passes are
    # one indistinguishable population. Appended AFTER the existing columns so an older
    # reader of this JSONL keeps working (it reads by key, and dicts tolerate a new one).
    caller: str = ""
    extra: dict = field(default_factory=dict)

    def to_json_line(self) -> str:
        d = asdict(self)
        # ``extra`` is spread inline so ad-hoc fields read like columns; drop it
        # when empty so the common row stays lean.
        extra = d.pop("extra", None) or {}
        d.update(extra)
        return json.dumps(d, separators=(",", ":"), default=str)


def now_ms() -> float:
    """Monotonic-ish millisecond clock for latency spans (wall clock for ``ts``)."""
    return time.monotonic() * 1000.0


def record_attempt(rec: AttemptRecord) -> None:
    """Append one attempt row, trimming the file when it crosses 2× the cap.

    Best-effort and never raises: an audit-write failure must not break a model
    call (the call is the product; the audit is observability). Failures log at
    WARNING so a broken trail is diagnosable rather than silent.
    """
    try:
        if rec.caller and rec.caller not in CALLERS:
            # Rejected, never written: a typo'd caller must not become a fifth spelling
            # inside the file, and a row silently attributed to a name no read surface
            # groups by is worse than one that admits it is unattributed. WARNING, not
            # DEBUG — a miswired binder is a defect the operator can fix.
            logger.warning(
                "model-call audit: unknown caller %r (expected one of %s) — "
                "recording this attempt unattributed",
                rec.caller,
                CALLERS,
            )
            rec = replace(rec, caller="")
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = rec.to_json_line() + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        _maybe_trim(path)
    except Exception:
        logger.warning("model-call audit write failed", exc_info=True)


def _maybe_trim(path: Path) -> None:
    """Rewrite ``path`` to its last ``_LINE_CAP`` lines once it exceeds 2× the cap."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    if len(lines) <= 2 * _LINE_CAP:
        return
    kept = lines[-_LINE_CAP:]
    atomic_write(path, "".join(kept))


def read_recent(limit: int = 1000) -> list[dict]:
    """Return up to ``limit`` most-recent attempt rows (oldest→newest), parsed.

    Powers the derived health view (§2.5). Malformed lines are skipped, not
    fatal — a partially-written tail must not blank the whole panel.
    """
    path = _audit_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    rows: list[dict] = []
    for raw in lines[-limit:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return rows
