"""Lesson confidence — one derivation, one gate, one precedence rule.

Before this module a lesson was injected at full strength the moment it was
written. A rule observed once by an inference pass and a rule the user has taught
and re-taught a hundred times entered the prompt identically, because the only
question anyone asked was *does a row exist*. Existence is not evidence, and a
prompt built on existence cannot answer either half of what a user actually asks:
"why is it still doing that" or "why did it stop doing that".

**Confidence is DERIVED, never assigned.** Nothing writes a confidence number.
:func:`derive` reads an evidence record — how many times the lesson was observed,
how recently, how often it was contradicted, whether a correction reversed it —
and computes the value fresh. `semantic_memory.confidence` (``1.0`` for
``user_explicit``, ``0.9`` otherwise) is a *source constant* used for write
conflict resolution and is deliberately NOT this number: it says who wrote the
row, not how well the row is supported.

**Retained-but-not-injected is a declared state, not deletion.** A lesson below
the gate stays in the store, keeps accumulating observations, and is reported as
:attr:`LessonStanding.RETAINED`. Discarding weak signal destroys exactly the
evidence that would later make it strong — the second time a rule is observed is
only meaningful because the first was kept.

**The precedence rule (stated ONCE — cite this docstring, do not restate it).**
When evidence both supports and refutes a lesson, refutation wins, in this order:

1. **A REVERSAL voids every observation that preceded it.** Corroboration counts
   only observations recorded *after* the last reversal, so a lesson the user
   un-taught must earn its evidence again from scratch rather than returning at
   the strength it held before.
2. **Each CONTRADICTION cancels one surviving observation** before the
   corroboration curve is read. A lesson observed three times and contradicted
   twice therefore stands exactly where a once-observed lesson stands, and never
   above it.
3. **Only then** do corroboration and recency multiply into a confidence.

The consequence is the property the atom asks for: a lesson can never be injected
alongside its own refutation, because refutation is *subtracted from* the
evidence rather than scored beside it.

**Why an OBSERVATION count and not a usage count.** ``learning.usage`` exempts
lessons from the usage store on purpose — a lesson renders as an always-on block,
so "surfaced" degenerates into "a session happened" and measures how much the
user talks. That exemption is not a claim that lessons have no evidence axis; it
names the wrong one. The right axis is the *capture* side: every time the world
produces this rule again, that is one observation, and the dedup path that used
to drop a repeat silently was throwing that signal away.

**Recency rides the ONE decay kernel.** :mod:`personalclaw.learning.decay` is the
single answer to "is this still relevant?", and a second private curve here would
be the third-implementation defect that module was written to end. Human-authored
lessons ride it at ``importance=1.0`` — five times slower, still not never, which
is that module's own "importance is an axis, not an exemption" doctrine rather
than a carve-out invented here.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from personalclaw.learning import decay
from personalclaw.learning.hygiene import MIN_EVIDENCE_DEFAULT

logger = logging.getLogger(__name__)

#: The decay kind lessons age under — the shared kernel's table, not a local rate.
DECAY_KIND = "lesson"

#: Importance a human-authored lesson carries into the decay kernel. 1.0 is the
#: kernel's maximum damping (λ cut to 20%), which is the strongest statement the
#: kernel can make short of an exemption it deliberately refuses to offer.
HUMAN_AUTHORED_IMPORTANCE = 1.0

#: Observations (beyond the first) that halve the remaining distance to certainty.
#: 2.0 is not a taste call: it is the value that makes ``corroboration()`` cross
#: 0.5 at exactly ``MIN_EVIDENCE_DEFAULT`` observations, so this curve and the
#: evidence floor the promotion ladder / pattern synthesis / inferred proposals
#: already share cannot disagree about what counts as corroborated.
CORROBORATION_HALFLIFE = 2.0


def corroboration(observations: int) -> float:
    """How corroborated ``observations`` sightings are, in [0.0, 1.0).

    ``1`` yields exactly ``0.0``: a single unrepeated observation is an anecdote,
    and an anecdote has no corroboration to report. The curve saturates rather
    than capping, so the hundredth sighting still counts for a little more than
    the tenth without ever reaching certainty.
    """
    if observations <= 0:
        return 0.0
    return 1.0 - 2.0 ** (-(observations - 1) / CORROBORATION_HALFLIFE)


#: The default injection floor. Stated with its reason, not picked: it is exactly
#: ``corroboration(MIN_EVIDENCE_DEFAULT)`` — the confidence an agent-inferred
#: lesson reaches at the third corroborating observation, which is the evidence
#: floor `learning.min_evidence` already declares for every other learning path
#: ("one is an anecdote and two a coincidence"). Choosing any other number here
#: would mean the injection gate and the evidence floor disagree about the same
#: question. ``test_lesson_confidence.py`` asserts the identity, so the default
#: cannot drift into a picked number without a red test.
DEFAULT_MIN_CONFIDENCE = corroboration(MIN_EVIDENCE_DEFAULT)


class LessonStanding(str, Enum):
    """Where a lesson stands relative to the injection gate.

    A closed enum rather than a bare boolean because "retained" is a *declared*
    state with its own meaning — still stored, still accumulating evidence, just
    not in the prompt — and a boolean would let a reader mistake it for absence.
    """

    #: Above the gate: this lesson is in the prompt.
    INJECTED = "injected"
    #: Below the gate: retained, still accumulating evidence, NOT in the prompt.
    RETAINED = "retained"


@dataclass(frozen=True)
class LessonEvidence:
    """What is known about one lesson's support. The only input to a confidence.

    ``voided`` is the observation count at the moment of the most recent reversal
    — step 1 of the precedence rule in the module docstring. Stored rather than
    recomputed because the observations themselves are counters, not rows: the
    only way to know which sightings preceded a reversal is to record where the
    line fell.
    """

    observations: int = 0
    contradictions: int = 0
    reversals: int = 0
    voided: int = 0
    human_authored: bool = False
    first_observed_at: str = ""
    last_observed_at: str = ""
    last_reversed_at: str = ""

    @property
    def surviving_observations(self) -> int:
        """Observations that survive the precedence rule (module docstring, 1+2)."""
        return max(0, self.observations - self.voided - self.contradictions)


@dataclass(frozen=True)
class LessonVerdict:
    """One lesson's derived confidence, its standing, and why.

    ``reason`` is a sentence a user can read. "Why did it stop doing that" is only
    answerable if the negative decision explains itself — the same rule
    ``GateReason`` follows for capture denials.
    """

    confidence: float
    standing: LessonStanding
    reason: str
    evidence: LessonEvidence

    @property
    def injected(self) -> bool:
        return self.standing is LessonStanding.INJECTED


def derive(evidence: LessonEvidence, *, active_days_idle: float = 0.0) -> float:
    """Derive a confidence in [0.0, 1.0] from evidence alone.

    Pure: no clock, no store, no config. The caller owns the calendar (the same
    contract ``decay.strength`` states) which is what lets the active-days clock
    exist and what makes this testable without freezing time.

    Applies the precedence rule from the module docstring in its stated order —
    reversal voids, contradictions cancel, then corroboration × recency.
    """
    surviving = evidence.surviving_observations
    if surviving <= 0:
        return 0.0
    # A lesson the user typed is an instruction, not a hypothesis awaiting
    # corroboration, so its first surviving observation is already full support.
    # It is NOT exempt from the rest: a reversal still voids it (checked above)
    # and recency still ages it (below).
    base = 1.0 if evidence.human_authored else corroboration(surviving)
    importance = HUMAN_AUTHORED_IMPORTANCE if evidence.human_authored else 0.0
    recency = decay.strength(
        kind=DECAY_KIND,
        active_days_since_use=active_days_idle,
        importance=importance,
    )
    return max(0.0, min(1.0, base * recency))


def classify(
    evidence: LessonEvidence,
    *,
    threshold: float = DEFAULT_MIN_CONFIDENCE,
    active_days_idle: float = 0.0,
) -> LessonVerdict:
    """Derive the confidence AND the standing, with a readable reason.

    One function so the gate and the user-facing report can never disagree: the
    number the Memory studio shows is the number the injection filter compared.
    """
    confidence = derive(evidence, active_days_idle=active_days_idle)
    if confidence >= threshold:
        return LessonVerdict(
            confidence,
            LessonStanding.INJECTED,
            _injected_reason(evidence, confidence, threshold),
            evidence,
        )
    return LessonVerdict(
        confidence,
        LessonStanding.RETAINED,
        _retained_reason(evidence, confidence, threshold),
        evidence,
    )


def configured_threshold() -> float:
    """The live injection floor from ``learning.min_lesson_confidence``.

    The config READER for that field. Clamped to [0.0, 1.0] here rather than only
    at the PATCH bounds, because ``config.json`` is hand-editable and a negative
    floor is not a looser gate — it is no gate, which would silently restore the
    inject-on-existence behaviour this module replaces. A config load that fails
    falls back to the stated default rather than to "inject everything".
    """
    try:
        from personalclaw.config.loader import AppConfig

        raw = getattr(AppConfig.load().learning, "min_lesson_confidence", DEFAULT_MIN_CONFIDENCE)
        value = float(raw)
    except Exception:
        logger.debug("min_lesson_confidence read failed; using the default", exc_info=True)
        return DEFAULT_MIN_CONFIDENCE
    return max(0.0, min(1.0, value))


def _injected_reason(evidence: LessonEvidence, confidence: float, threshold: float) -> str:
    if evidence.human_authored:
        return f"you taught this directly — {confidence:.0%} confidence (gate {threshold:.0%})"
    return (
        f"observed {evidence.surviving_observations}× — "
        f"{confidence:.0%} confidence (gate {threshold:.0%})"
    )


def _retained_reason(evidence: LessonEvidence, confidence: float, threshold: float) -> str:
    """Why this lesson is held back. Ordered like the precedence rule it reports."""
    if evidence.reversals and evidence.observations <= evidence.voided:
        return (
            f"reversed — the {evidence.voided} earlier observation(s) no longer count; "
            f"held below the {threshold:.0%} gate until re-observed"
        )
    if evidence.contradictions and evidence.surviving_observations <= 0:
        return (
            f"contradicted {evidence.contradictions}× — no surviving corroboration; "
            f"held below the {threshold:.0%} gate"
        )
    if evidence.observations <= 0:
        return f"no recorded observation yet — held below the {threshold:.0%} gate"
    contradicted = f", contradicted {evidence.contradictions}×" if evidence.contradictions else ""
    return (
        f"observed {evidence.surviving_observations}×{contradicted} — {confidence:.0%} "
        f"confidence, below the {threshold:.0%} gate; retained and still accumulating"
    )


# ── Persistence ──


class LessonEvidenceStore:
    """Evidence counters for lessons, in ``learning.db`` beside the staging log.

    Deliberately NOT in ``memory.db`` next to the lessons themselves: this is a
    learning-side observation log, it is written on a hot path, and a corrupt
    counter file must never take semantic memory down with it — the same reason
    the staging log lives here.
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        from personalclaw.learning.staging import StagingStore

        self._staging = StagingStore(base_dir)
        self._lock = threading.RLock()
        self._bootstrapped = False

    @property
    def path(self) -> Path:
        return self._staging.path

    def close(self) -> None:
        self._staging.close()

    def _ensure(self) -> None:
        if self._bootstrapped:
            return
        with self._staging._cursor() as cur:
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS lesson_evidence (
                    lesson_key        TEXT PRIMARY KEY,
                    observations      INTEGER NOT NULL DEFAULT 0,
                    contradictions    INTEGER NOT NULL DEFAULT 0,
                    reversals         INTEGER NOT NULL DEFAULT 0,
                    voided            INTEGER NOT NULL DEFAULT 0,
                    human_authored    INTEGER NOT NULL DEFAULT 0,
                    first_observed_at TEXT NOT NULL DEFAULT '',
                    last_observed_at  TEXT NOT NULL DEFAULT '',
                    last_reversed_at  TEXT NOT NULL DEFAULT ''
                );
                -- The vacation-proof clock, shared with `usage.UsageStore` which
                -- also creates it. Two idempotent creators rather than one store
                -- depending on the other having opened the db first: whichever
                -- gets there first wins, and neither can be the reason the other
                -- reads a missing table.
                CREATE TABLE IF NOT EXISTS active_days (
                    day TEXT PRIMARY KEY
                );
                """)
        self._bootstrapped = True

    # ── Recording ──

    def record_observation(self, lesson_key: str, *, human_authored: bool = False) -> int:
        """Count one sighting of ``lesson_key``. Returns the new observation total.

        Called for EVERY write that resolves to this lesson — a fresh insert, a
        write the dedup pass suppressed, and the winner of a supersession alike.
        The suppressed case is the one that matters: that repeat used to return
        ``False`` and vanish, which is how a rule the world produced ten times
        stayed indistinguishable from one produced once.

        ``human_authored`` is sticky: once the user has taught a rule directly, a
        later inference pass observing the same rule must not demote it to a
        hypothesis.
        """
        if not lesson_key:
            return 0
        self._ensure()
        now = _now()
        with self._lock, self._staging._cursor() as cur:
            cur.execute(
                "INSERT INTO lesson_evidence "
                "(lesson_key, observations, human_authored, first_observed_at, last_observed_at) "
                "VALUES (?, 1, ?, ?, ?) "
                "ON CONFLICT(lesson_key) DO UPDATE SET "
                "observations = observations + 1, "
                "human_authored = MAX(human_authored, excluded.human_authored), "
                "last_observed_at = excluded.last_observed_at;",
                (lesson_key, 1 if human_authored else 0, now, now),
            )
            row = cur.execute(
                "SELECT observations FROM lesson_evidence WHERE lesson_key = ?;", (lesson_key,)
            ).fetchone()
        return int(row[0]) if row else 0

    def record_contradiction(self, lesson_key: str) -> None:
        """Record that a later observation contradicted ``lesson_key``.

        Step 2 of the precedence rule (module docstring): this cancels one
        surviving observation, so the contradicted lesson loses confidence rather
        than sitting in the prompt beside its own refutation.
        """
        if not lesson_key:
            return
        self._ensure()
        now = _now()
        with self._lock, self._staging._cursor() as cur:
            cur.execute(
                "INSERT INTO lesson_evidence (lesson_key, contradictions, last_observed_at) "
                "VALUES (?, 1, ?) "
                "ON CONFLICT(lesson_key) DO UPDATE SET contradictions = contradictions + 1;",
                (lesson_key, now),
            )

    def record_reversal(self, lesson_key: str) -> None:
        """Record that a correction REVERSED ``lesson_key`` — the user un-taught it.

        Step 1 of the precedence rule (module docstring): every observation up to
        now is voided. The row is kept, not deleted, because the counters are the
        answer to "why did it stop doing that" — and because the same
        deterministic key comes back if the rule is ever written again, at which
        point it must re-earn its evidence rather than resume at its old strength.
        """
        if not lesson_key:
            return
        self._ensure()
        now = _now()
        with self._lock, self._staging._cursor() as cur:
            cur.execute(
                "INSERT INTO lesson_evidence "
                "(lesson_key, reversals, voided, last_reversed_at) VALUES (?, 1, 0, ?) "
                "ON CONFLICT(lesson_key) DO UPDATE SET "
                "reversals = reversals + 1, voided = observations, last_reversed_at = ?;",
                (lesson_key, now, now),
            )

    def carry_forward(self, old_key: str, new_key: str) -> None:
        """Move ``old_key``'s evidence onto the lesson that superseded it.

        A supersession is the same rule said better, so its corroboration belongs
        to the survivor. Dropping it would make "restate a lesson more precisely"
        an evidence reset — the user would watch a well-supported rule fall out of
        the prompt for having been improved.
        """
        if not old_key or not new_key or old_key == new_key:
            return
        old = self.evidence_for(old_key)
        if old.observations <= 0 and old.contradictions <= 0 and old.reversals <= 0:
            return
        self._ensure()
        now = _now()
        with self._lock, self._staging._cursor() as cur:
            cur.execute(
                "INSERT INTO lesson_evidence (lesson_key, observations, contradictions, "
                "reversals, voided, human_authored, first_observed_at, last_observed_at, "
                "last_reversed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(lesson_key) DO UPDATE SET "
                "observations = observations + excluded.observations, "
                "contradictions = contradictions + excluded.contradictions, "
                "reversals = reversals + excluded.reversals, "
                "voided = voided + excluded.voided, "
                "human_authored = MAX(human_authored, excluded.human_authored), "
                "last_observed_at = excluded.last_observed_at;",
                (
                    new_key,
                    old.observations,
                    old.contradictions,
                    old.reversals,
                    old.voided,
                    1 if old.human_authored else 0,
                    old.first_observed_at or now,
                    old.last_observed_at or now,
                    old.last_reversed_at,
                ),
            )
            cur.execute("DELETE FROM lesson_evidence WHERE lesson_key = ?;", (old_key,))

    # ── Reading ──

    def evidence_for(self, lesson_key: str) -> LessonEvidence:
        """One lesson's evidence. An absent row reads as zero evidence, not an error."""
        if not lesson_key:
            return LessonEvidence()
        self._ensure()
        with self._staging._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM lesson_evidence WHERE lesson_key = ?;", (lesson_key,)
            ).fetchone()
        return _to_evidence(row) if row else LessonEvidence()

    def evidence_map(self, lesson_keys: list[str]) -> dict[str, LessonEvidence]:
        """Evidence for many lessons in ONE query — the injection filter's read.

        A per-lesson query on the render path would put fifty round-trips in front
        of every turn.
        """
        keys = [k for k in lesson_keys if k]
        if not keys:
            return {}
        self._ensure()
        placeholders = ",".join("?" for _ in keys)
        # The interpolation is a generated run of `?` placeholders, one per key; every
        # key itself is bound. There is no other way to spell a variadic IN in sqlite3.
        sql = f"SELECT * FROM lesson_evidence WHERE lesson_key IN ({placeholders});"  # noqa: S608
        with self._staging._cursor() as cur:
            rows = cur.execute(sql, tuple(keys)).fetchall()
        return {str(r["lesson_key"]): _to_evidence(r) for r in rows}

    def active_days(self) -> list[str]:
        """Every day the user was present — the vacation-proof recency clock.

        Empty when nothing has marked a day yet (a young install, or a session
        that never flushed usage). That reads as ZERO idle days, so recency is
        1.0: a missing clock must never be the reason every lesson silently drops
        out of the prompt.
        """
        self._ensure()
        with self._staging._cursor() as cur:
            return [str(r[0]) for r in cur.execute("SELECT day FROM active_days ORDER BY day;")]

    def idle_active_days(self, evidence: LessonEvidence) -> float:
        """Active days since this lesson was last observed."""
        if not evidence.last_observed_at:
            return 0.0
        return decay.active_days_between(self.active_days(), evidence.last_observed_at)


def _to_evidence(row: object) -> LessonEvidence:
    def _int(name: str) -> int:
        try:
            return int(row[name])  # type: ignore[index]
        except (KeyError, IndexError, TypeError, ValueError):
            return 0

    def _str(name: str) -> str:
        try:
            return str(row[name] or "")  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return ""

    return LessonEvidence(
        observations=_int("observations"),
        contradictions=_int("contradictions"),
        reversals=_int("reversals"),
        voided=_int("voided"),
        human_authored=bool(_int("human_authored")),
        first_observed_at=_str("first_observed_at"),
        last_observed_at=_str("last_observed_at"),
        last_reversed_at=_str("last_reversed_at"),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Module-level accessor ──

_INSTANCES: dict[str, LessonEvidenceStore] = {}
_INSTANCE_LOCK = threading.Lock()


def get_store(base_dir: Path | str | None = None) -> LessonEvidenceStore:
    """The shared evidence store for one home directory.

    Cached PER DIRECTORY rather than process-globally: the writer
    (``VectorMemoryStore``) derives this directory from its own ``memory.db``
    location, so a test pointing at ``tmp_path`` gets a store beside its own
    database instead of one bound to the real home at import time.
    """
    key = str(Path(base_dir).resolve()) if base_dir is not None else ""
    with _INSTANCE_LOCK:
        store = _INSTANCES.get(key)
        if store is None:
            store = LessonEvidenceStore(base_dir)
            _INSTANCES[key] = store
        return store


def reset_store() -> None:
    """Drop every cached instance (tests, and home-directory switches)."""
    with _INSTANCE_LOCK:
        for store in _INSTANCES.values():
            try:
                store.close()
            except Exception:  # pragma: no cover - close on a dead handle
                logger.debug("lesson evidence store close failed", exc_info=True)
        _INSTANCES.clear()
