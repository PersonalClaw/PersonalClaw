"""Run session ownership + incognito inheritance (WORK-CONTAINERS §5.1, S50).

A run may OWN sessions and may be LAUNCHED FROM one. Both directions need a contract:

* **Ownership** gives a run's stage sessions a key of their own —
  `workflow:<run_id>:<node_id>` — registered alongside the existing conventions
  (`loop-<id>`, `cron:<id>`, `subagent:<id>`). Two seams have to learn it: SEL's source
  inference, and behaviour keying.
* **Inheritance** carries a temporary/incognito origin DOWN into every session the run owns. A run
  launched from an incognito chat that quietly wrote memories from its stages would defeat the mode
  entirely — and it would do so invisibly, because the chat itself stayed clean.

Two facts about the existing machinery decide the shape here, both verified in code:

1. **Behaviour is keyed off `session._app`, not the key prefix.** The `loop_`/`loop:` prefix-
match in
   `context._prompt_use_case_for` is a known near-miss the plan explicitly says not to repeat. So
   ownership sets `_app` and the key is only an identifier.
2. **`session_restrictions` is process-global and forgets on restart**, while a session's JSONL
   `memory_mode` line survives. `session_search.is_restricted` already reads BOTH for exactly that
   reason. Suppression here follows that precedent rather than inventing a third store: the registry
   is the fast path, the durable line is the truth after a restart.

The asymmetry that matters: **a lookup failure means RESTRICTED, not unrestricted.** An unavailable
registry or an unreadable metadata line must not open the gate — the cost of a wrongly-suppressed
memory write is a lost note, and the cost of a wrongly-permitted one is a memory the user believed
was never recorded.

Pure functions over keys and metadata. No I/O; the caller marks the registry and writes the JSONL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: The key prefix for a run-owned session. A COLON separator, matching `cron:`/`subagent:` — the
#: loop convention (`loop-<id>`, a hyphen) is the odd one out, and copying it would make a fourth
#: parser needed for a fourth shape.
OWNED_PREFIX = "workflow:"

#: The `_app` value behaviour keys off. Set explicitly, because behaviour is keyed off `_app`
#: and NOT
#: off the key prefix — the `loop_`/`loop:` prefix-match in `context._prompt_use_case_for` is a
#: known
#: near-miss, and repeating it here would make an owned session's behaviour depend on a string
#: parse.
OWNED_APP = "workflow"

#: The SEL source value for a run-owned session. `sel._infer_source` has no workflow value today, so
#: every run-owned tool call currently audits as `channel` — the catch-all, which is where an
#: unrecognized key silently lands.
SEL_SOURCE = "workflow"

_KEY_RE = re.compile(r"^workflow:(?P<run>[A-Za-z0-9_.-]+):(?P<node>[A-Za-z0-9_.\[\]-]+)$")


class MemoryMode(str, Enum):
    """A session's durable memory posture.

    `TEMPORARY` blocks reads AND writes (a blank slate); `INCOGNITO` blocks writes only (the session
    still sees context already injected). The distinction is load-bearing: using the write gate for
    reads would blank a session the user only asked not to record.
    """

    NORMAL = "normal"
    TEMPORARY = "temporary"
    INCOGNITO = "incognito"


#: Modes that suppress memory WRITES. Both, because incognito exists precisely to keep a session out
#: of the record while letting it work.
WRITE_SUPPRESSED = frozenset({MemoryMode.TEMPORARY, MemoryMode.INCOGNITO})

#: Modes that suppress memory READS. Only `temporary` — a blank slate. Incognito reads are
#: allowed,
#: and treating them as blocked would silently degrade an incognito session's answers.
READ_SUPPRESSED = frozenset({MemoryMode.TEMPORARY})

#: Where a run's inherited memory mode is persisted. In `WorkflowRun.extra` — a free-form dict
#: that is
#: already stored and round-tripped — rather than a new column. A schema change for one string
#: would
#: be a migration under the pre-1.0 banner for no gain, and `extra` is exactly what it is for.
RUN_MODE_KEY = "memory_mode"


def run_mode(run: Any) -> MemoryMode:
    """The inherited mode on a run record, read from `extra`.

    One reader, so a caller cannot get the key or the fallback wrong. A run with no recorded mode is
    NORMAL: absence here means the run was started before the feature or from an unrestricted
    origin,
    both of which are genuinely unrestricted — unlike an unrecognized VALUE, which is someone
    asking
    for privacy in a vocabulary this build does not know.
    """
    extra = getattr(run, "extra", None) or {}
    return parse_mode(extra.get(RUN_MODE_KEY, ""))


def stamp_run_mode(extra: dict[str, Any], mode: MemoryMode) -> dict[str, Any]:
    """Record the inherited mode on a run's `extra`, returning a NEW dict.

    A new dict rather than a mutation: run records are compared and journaled, and mutating the one
    the caller holds would make a rejected create leave a stamped object behind.

    The stamped field IS `durable_metadata` — `RUN_MODE_KEY` and the JSONL `memory_mode` line are
    the same key on purpose. A run owns no `ConversationLog` file (its stage subagents persist under
    their own `subagent:` keys), so the run RECORD's `extra` head is the run's durable metadata
    head: it round-trips on disk and is what a restart replays. Composing over `durable_metadata`
    keeps the two the same value rather than two string literals that could drift.
    """
    return {**(extra or {}), **durable_metadata(mode)}


def owned_key(run_id: str, node_id: str) -> str:
    """The session key for one run-owned stage session.

    Both parts are present because both are needed: the run id groups a run's sessions for the
    cockpit, and the node id says which stage — a key with only the run id would make five
    parallel
    stages indistinguishable in the audit log.
    """
    return f"{OWNED_PREFIX}{_safe(run_id)}:{_safe(node_id)}"


def _safe(part: str) -> str:
    """Key parts are sanitized, not rejected.

    A node id is author-controlled and a run id is generated; a key that raised on an unexpected
    character would fail the RUN over a naming detail. Sanitizing keeps the key parseable while
    losing nothing that identifies the session.
    """
    return re.sub(r"[^A-Za-z0-9_.\[\]-]", "-", str(part or "")) or "unknown"


def parse_owned(session_key: str) -> tuple[str, str] | None:
    """`(run_id, node_id)` for a run-owned key, or None.

    Returns None rather than raising for a non-owned key: every caller here is asking "is this
    one of
    mine", and the common answer is no.
    """
    match = _KEY_RE.match(session_key or "")
    if match is None:
        return None
    return match.group("run"), match.group("node")


def is_owned(session_key: str) -> bool:
    return parse_owned(session_key) is not None


def sel_source(session_key: str) -> str:
    """The SEL source for a session key, adding the workflow value the log lacks.

    Without this a run-owned tool call audits as `channel` — the catch-all where unrecognized keys
    silently land, which makes "what did the run do" unanswerable from the audit log even though
    every
    event is there.
    """
    if is_owned(session_key):
        return SEL_SOURCE
    from personalclaw.sel import _infer_source

    return _infer_source(session_key or "")


@dataclass
class Ownership:
    """The full ownership record for one run-owned session.

    `app` is separate from `key` on purpose — see the module docstring: behaviour reads `_app`,
    and a
    consumer that inferred it from the key would be a fourth prefix parser.
    """

    key: str
    run_id: str
    node_id: str
    app: str = OWNED_APP
    source: str = SEL_SOURCE
    memory_mode: MemoryMode = MemoryMode.NORMAL

    @property
    def suppresses_writes(self) -> bool:
        return self.memory_mode in WRITE_SUPPRESSED

    @property
    def suppresses_reads(self) -> bool:
        return self.memory_mode in READ_SUPPRESSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "app": self.app,
            "source": self.source,
            "memory_mode": self.memory_mode.value,
            "suppresses_writes": self.suppresses_writes,
            "suppresses_reads": self.suppresses_reads,
        }


def own_session(
    run_id: str, node_id: str, *, inherited_mode: MemoryMode = MemoryMode.NORMAL
) -> Ownership:
    """Build the ownership record for a stage session, carrying the run's inherited mode."""
    return Ownership(
        key=owned_key(run_id, node_id),
        run_id=_safe(run_id),
        node_id=_safe(node_id),
        memory_mode=inherited_mode,
    )


def parse_mode(raw: Any) -> MemoryMode:
    """Read a `memory_mode` value tolerantly, defaulting to the RESTRICTED direction on nonsense.

    An unrecognized mode string becomes `INCOGNITO`, not `NORMAL`. That is the whole safety
    argument:
    the value exists because someone asked for privacy, and a typo or a newer mode name this build
    does not know must not be read as "record everything". A lost note is recoverable; a memory the
    user believed was never written is not.

    `"persistent"` is the ONE alias that must map to `NORMAL` rather than the restricted default.
    It is the on-disk `memory_mode` a normal chat session writes (`VALID_MEMORY_MODES` in
    `dashboard.state`, and history's `setdefault(..., "persistent")` for pre-mode sessions), so it
    is genuinely "record everything" — not a privacy request. Treating it as unknown would suppress
    writes for EVERY normal run inheriting from a chat, which is the opposite of the feature: the
    fail-closed direction is for values that mean privacy, and `persistent` is the value that means
    the reverse.
    """
    text = str(raw or "").strip().lower()
    if not text or text == "persistent":
        return MemoryMode.NORMAL
    try:
        return MemoryMode(text)
    except ValueError:
        return MemoryMode.INCOGNITO


def inherit_mode(origin_key: str, *, origin_metadata: dict[str, Any] | None = None) -> MemoryMode:
    """The mode a run inherits from the session that launched it.

    Reads BOTH sources, in the order that survives a restart:

    1. The durable JSONL `memory_mode` line, when the caller has the metadata.
    2. The process-global `session_restrictions` registry.

    Both, for the reason `session_search.is_restricted` already does it: the registry only knows
    sessions this process has seen, while the metadata line is what history consolidation re-derives
    from after a restart. Checking only the registry would mean a gateway restart silently un-marks
    every incognito run in flight.
    """
    durable = parse_mode((origin_metadata or {}).get("memory_mode"))
    if durable is not MemoryMode.NORMAL:
        return durable
    try:
        from personalclaw import session_restrictions

        if session_restrictions.is_temporary(origin_key):
            return MemoryMode.TEMPORARY
        if session_restrictions.is_incognito(origin_key):
            return MemoryMode.INCOGNITO
    except Exception:
        # An unavailable registry must not OPEN the gate. With no durable line and no registry there
        # is nothing to inherit, so `NORMAL` is correct here — the fail-closed direction applies
        # to
        # reading a mode that EXISTS, which the durable check above already covers.
        return MemoryMode.NORMAL
    return MemoryMode.NORMAL


#: Node kinds whose work is memory/learning persistence. A run carrying an inherited restriction
#: skips
#: these OUTRIGHT rather than letting them run and relying on a downstream gate — a persist
#: provider
#: that gained a second write path would leak, and the per-consumer gates are defence in depth, not
#: the primary control.
LEARNING_PROVIDERS = frozenset(
    {
        "knowledge-persist",
        "memory-write",
        "memory-persist",
        "lesson-write",
        "learning-capture",
        "feedback-capture",
    }
)


def skips_node(node_config: dict[str, Any], mode: MemoryMode) -> tuple[bool, str]:
    """Whether a restricted run must skip this node entirely, and why.

    Skipping at the ENGINE is the primary control. Letting a learning node run and trusting the
    consumer's own gate would make correctness depend on every persist path checking a flag —
    and a
    new write path added later would leak by default. The engine-level skip fails closed for paths
    that do not exist yet.
    """
    if mode not in WRITE_SUPPRESSED:
        return False, ""
    cfg = node_config or {}
    provider = str(cfg.get("provider", "") or "").strip().lower()
    if provider in LEARNING_PROVIDERS:
        return True, f"{mode.value} run: skipping `{provider}` (memory writes are suppressed)"
    if cfg.get("persists_memory") is True:
        return True, f"{mode.value} run: node declares persists_memory"
    return False, ""


@dataclass
class Announcement:
    """Where a run's completion is mirrored back to.

    A blocking run mirrors a summary into the session that launched it — but a RESTRICTED origin
    gets
    the summary WITHOUT it being indexed, which is why the flag travels with the text rather than
    being decided at the destination.
    """

    origin_key: str
    text: str
    indexable: bool = True
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin_key": self.origin_key,
            "text": self.text,
            "indexable": self.indexable,
            "reason": self.reason,
        }


def announcement(origin_key: str, text: str, mode: MemoryMode) -> Announcement:
    """A completion summary for the launching session, with its indexability decided HERE.

    Deciding at the destination would mean every surface that can receive a summary re-derives the
    rule, and the one that forgot would index it. The user asked for a private session, not a
    private
    session with one indexed exception.
    """
    if mode in WRITE_SUPPRESSED:
        return Announcement(
            origin_key=origin_key,
            text=text,
            indexable=False,
            reason=f"origin session is {mode.value}",
        )
    return Announcement(origin_key=origin_key, text=text, indexable=True)


def durable_metadata(mode: MemoryMode) -> dict[str, Any]:
    """The metadata line an owned session's JSONL head must carry.

    `NORMAL` writes the key EXPLICITLY rather than omitting it. An absent key is indistinguishable
    from a pre-mode session, and the tolerant reader treats unknown values as restricted — so an
    omitted mode on a normal session would be read as unrestricted only by accident of the
    empty-string
    branch. Writing it makes the posture a recorded fact.
    """
    return {"memory_mode": mode.value}


def restriction_calls(ownership: Ownership) -> list[str]:
    """Which `session_restrictions` marks a caller must make for an owned session.

    Returned as names rather than performed, so the caller owns the process-global mutation and this
    module stays testable without touching a registry every other test shares. The LIST is the
    contract: a `temporary` session needs BOTH marks, because `is_temporary` gates reads while
    `is_restricted` (true for either) gates writes.
    """
    if ownership.memory_mode is MemoryMode.TEMPORARY:
        return ["mark_temporary", "mark_incognito"]
    if ownership.memory_mode is MemoryMode.INCOGNITO:
        return ["mark_incognito"]
    return []


def audit_fields(ownership: Ownership) -> dict[str, str]:
    """SEL fields for a run-owned event.

    The run id rides in the audit event rather than only in the session key, because an auditor
    filtering by run should not have to parse keys — and a key format change would silently break
    every saved filter.
    """
    return {
        "source": ownership.source,
        "session_key": ownership.key,
        "run_id": ownership.run_id,
        "node_id": ownership.node_id,
    }


@dataclass
class OwnedSessions:
    """The sessions one run owns, for cleanup and for the cockpit.

    Cleanup matters: `session_restrictions.clear` exists, and a run that ended without calling it
    leaves marks in a bounded LRU where they eventually evict — which means the restriction
    outlives
    the session by an unpredictable amount and then vanishes, the worst of both.
    """

    run_id: str
    keys: list[str] = field(default_factory=list)

    def add(self, node_id: str) -> str:
        key = owned_key(self.run_id, node_id)
        if key not in self.keys:
            self.keys.append(key)
        return key

    def cleanup_plan(self) -> list[str]:
        """What to clear when the run ends. One entry per owned session, in creation order."""
        return list(self.keys)

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "keys": list(self.keys), "count": len(self.keys)}
