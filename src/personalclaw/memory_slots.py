"""Memory slots — bounded, always-injected registers (MEMORY-GRAPH-AND-VAULT §6/§6.1 — MGAV-8).

A slot is the one memory class that does NOT compete for recall. Facts, lessons and episodes
are *retrieved* when a query looks like them; a slot is a register the harness reads on every
session regardless of what the user asked. That makes slots the highest-leverage and the most
dangerous memory class at once, so the three properties below are enforced mechanically here
rather than left to whoever writes the next caller.

**1. Every slot is capped, and over-cap FAILS LOUDLY.** An always-injected register with no
ceiling is a slow context leak: each append costs every future turn, forever. The obvious
implementations are both wrong — silently truncating destroys what the user just said, and
silently dropping the write makes the system look like it agreed. So an over-cap append raises
:class:`SlotCapExceeded` carrying a :class:`TrimProposal` that names the exact lines whose
removal would make room. The caller must surface it; nothing here decides on the user's behalf
which memory to lose.

**2. Built-ins are LAZY.** The six built-in slots (persona / preferences / pending_items /
self_notes / glossary / self_model) are *descriptors*, not rows. Nothing is written until a
first real append, so a fresh install has zero `slot.*` rows and the injected block is empty
rather than six confusing empty headers. Materialising them eagerly would also mean every
install ships rows nobody wrote, which `audit_home` would then have to explain.

**3. Appends never resurrect a human tombstone.** A tombstoned line stays in the row (that is
what makes the guard possible) with ``tombstoned=True``, and :func:`append` refuses to re-add
text a HUMAN tombstoned. An agent-driven reflection hook that re-derives the same line the user
just deleted is not a bug the user can see — it looks like the system ignoring them — which is
why the check lives at the write primitive and not in each hook.

Writes go through the store's ``set_semantic``, so the memory event log (WAL) and
``undo_event`` cover slot writes with no separate journal. Reads and pure decisions live here;
this module builds no store and imports none, so ``vector_memory`` can depend on it one-way for
its put-time cap check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

#: Key prefix for every slot record. Allowlisted as ``slot.*`` in
#: ``vector_memory._BUILTIN_PREFIXES`` and excluded from ``_NON_FACT_KEY_CLAUSE`` — a slot is
#: injected by its OWN block, and rendering it again as a "fact about the user" would both
#: double-charge the budget and miscategorise the harness-facing slots (self_notes, self_model).
SLOT_PREFIX = "slot."

#: Cap for a slot with no explicit spec (an ad-hoc ``slot.<name>`` a caller invents). Chosen
#: small on purpose: an unknown slot has no budget argument behind it, so it gets the floor.
DEFAULT_SLOT_CAP_CHARS = 600

#: Hard ceiling for the ONE assembled Slots block (§6). Independent of the per-slot caps and
#: deliberately smaller than their sum: the per-slot cap bounds one register's growth, this
#: bounds what any combination of them can cost a turn. Both are needed — six slots each just
#: under their own cap would otherwise be a compliant 4kB block.
SLOTS_BLOCK_MAX_CHARS = 1400

_TRUNCATION_MARKER = "\n… [slots truncated]"


@dataclass(frozen=True)
class SlotSpec:
    """A built-in slot's identity and budget. A descriptor only — see the module docstring on
    laziness: holding a spec never implies a row exists."""

    name: str
    title: str
    cap_chars: int
    scope: str = "global"
    description: str = ""


#: The six built-in slots (§6). Caps are not uniform because the classes are not comparable:
#: `persona` and `preferences` are read on literally every turn and must stay tight, while
#: `pending_items` is a working list whose usefulness IS its length. `glossary` is the only
#: workspace-scoped one — a project's vocabulary is meaningless in another checkout, and a
#: global glossary would leak one client's terms into another's session.
BUILTIN_SLOTS: dict[str, SlotSpec] = {
    "persona": SlotSpec(
        name="persona",
        title="Persona",
        cap_chars=400,
        description="How the user wants the assistant to present itself.",
    ),
    "preferences": SlotSpec(
        name="preferences",
        title="Preferences",
        cap_chars=500,
        description="Standing preferences the assistant should default to.",
    ),
    "pending_items": SlotSpec(
        name="pending_items",
        title="Pending items",
        cap_chars=700,
        description="Open threads the user expects to be picked back up.",
    ),
    "self_notes": SlotSpec(
        name="self_notes",
        title="Self notes",
        cap_chars=500,
        description="The assistant's own working notes about this user's setup.",
    ),
    "glossary": SlotSpec(
        name="glossary",
        title="Glossary",
        cap_chars=600,
        scope="workspace",
        description="Project vocabulary: terms that mean something specific here.",
    ),
    "self_model": SlotSpec(
        name="self_model",
        title="Self model",
        cap_chars=500,
        description="Behavioural principles reinforced enough to act on (see learning.self_model).",
    ),
}

#: Render order for the assembled block. Explicit rather than `BUILTIN_SLOTS.keys()` so a slot
#: added to the registry does not silently change what the model reads first.
BLOCK_ORDER = (
    "persona",
    "preferences",
    "self_model",
    "glossary",
    "pending_items",
    "self_notes",
)


def key_for(name: str) -> str:
    """The memory key for a slot name. ``persona`` → ``slot.persona``."""
    return f"{SLOT_PREFIX}{name}"


def name_from_key(key: str) -> str:
    """The slot name inside a key, or ``""`` when *key* is not a slot key."""
    if not key.startswith(SLOT_PREFIX):
        return ""
    return key[len(SLOT_PREFIX) :]


def spec_for(name: str) -> SlotSpec:
    """The spec for *name*, synthesising a default-capped one for an ad-hoc slot."""
    known = BUILTIN_SLOTS.get(name)
    if known is not None:
        return known
    return SlotSpec(
        name=name,
        title=name.replace("_", " ").strip().capitalize() or name,
        cap_chars=DEFAULT_SLOT_CAP_CHARS,
    )


def cap_for(name: str) -> int:
    """The per-slot character cap for *name*."""
    return spec_for(name).cap_chars


def cap_for_key(key: str) -> int:
    """The per-slot cap for a full ``slot.<name>`` key."""
    return cap_for(name_from_key(key))


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass(frozen=True)
class SlotLine:
    """One line in a slot. Immutable: a change is an append plus a tombstone, never an edit,
    so the event log reads as a history instead of a series of overwrites."""

    text: str
    added_at: str = ""
    tombstoned: bool = False
    #: Who tombstoned it. ``"human"`` is the one value :func:`append` treats as final — an
    #: agent tombstone is a guess and may be re-derived, a human one may not.
    tombstoned_by: str = ""
    #: How many times a reflection pass has re-observed this line. Read by
    #: ``learning.self_model`` for the promotion threshold; carried here so the count survives
    #: in the same append-only record as the text.
    reinforcements: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "added_at": self.added_at,
            "tombstoned": self.tombstoned,
            "tombstoned_by": self.tombstoned_by,
            "reinforcements": self.reinforcements,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "SlotLine":
        if isinstance(raw, str):
            return cls(text=raw)
        if not isinstance(raw, dict):
            return cls(text=str(raw))
        return cls(
            text=str(raw.get("text", "")),
            added_at=str(raw.get("added_at", "")),
            tombstoned=bool(raw.get("tombstoned", False)),
            tombstoned_by=str(raw.get("tombstoned_by", "")),
            reinforcements=int(raw.get("reinforcements", 1) or 1),
        )


def parse_lines(value: Any) -> list[SlotLine]:
    """Every line in a stored slot value, tombstones included.

    Tolerates the three shapes a hand-edited memory.db can hold — the canonical
    ``{"lines": [...]}``, a bare list, and a plain string — because refusing to read a row a
    user edited by hand would make the slot silently vanish from their context."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [SlotLine(text=text)] if text else []
    raw: Any = value
    if isinstance(value, dict):
        raw = value.get("lines", [])
    if not isinstance(raw, list):
        return []
    out = [SlotLine.from_dict(item) for item in raw]
    return [line for line in out if line.text.strip()]


def live_lines(lines: list[SlotLine]) -> list[SlotLine]:
    """The lines that inject — i.e. not tombstoned."""
    return [line for line in lines if not line.tombstoned]


def to_value(lines: list[SlotLine]) -> dict[str, Any]:
    """The canonical stored shape for a slot's lines."""
    return {"lines": [line.to_dict() for line in lines]}


def live_chars(value: Any) -> int:
    """Character cost of a stored slot value — live lines only, joined by newlines.

    Tombstones are excluded because they cost the model nothing; they are retained in the row
    only so :func:`append` can refuse to resurrect them, and that retention is bounded by
    ``vector_memory._MAX_VALUE_BYTES`` rather than by the slot cap."""
    live = live_lines(parse_lines(value))
    if not live:
        return 0
    return sum(len(line.text) for line in live) + (len(live) - 1)


@dataclass(frozen=True)
class TrimProposal:
    """What the user would have to give up for an over-cap append to fit.

    A proposal, not an action, for the reason the module docstring gives: choosing which of the
    user's own lines to delete is not a decision a size check gets to make."""

    slot: str
    cap_chars: int
    current_chars: int
    incoming_chars: int
    drop_candidates: list[str] = field(default_factory=list)

    @property
    def over_by(self) -> int:
        return max(0, self.current_chars + self.incoming_chars - self.cap_chars)

    @property
    def message(self) -> str:
        return (
            f"slot {self.slot!r} is at {self.current_chars}/{self.cap_chars} chars and the new "
            f"line adds {self.incoming_chars}, exceeding the cap by {self.over_by}. "
            f"Nothing was written. Remove {len(self.drop_candidates)} line(s) to make room: "
            + "; ".join(repr(c) for c in self.drop_candidates)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "cap_chars": self.cap_chars,
            "current_chars": self.current_chars,
            "incoming_chars": self.incoming_chars,
            "over_by": self.over_by,
            "drop_candidates": list(self.drop_candidates),
            "message": self.message,
        }


class SlotCapExceeded(Exception):
    """Raised INSTEAD of writing when an append would breach the slot cap.

    Carries the :class:`TrimProposal` so a caller can show the user a real choice. Raising
    rather than returning a falsy value is deliberate: a silent ``False`` is exactly how a
    write gets dropped without anyone noticing."""

    def __init__(self, proposal: TrimProposal):
        super().__init__(proposal.message)
        self.proposal = proposal


def propose_trim(name: str, existing: list[SlotLine], incoming: str) -> TrimProposal:
    """The trim that would let *incoming* fit. Pure.

    Candidates are the OLDEST live lines first, and only as many as actually free enough room —
    proposing the whole slot when one line would do trains the user to accept blindly."""
    cap = cap_for(name)
    live = live_lines(existing)
    current = live_chars(to_value(existing))
    incoming_cost = len(incoming) + (1 if live else 0)
    need = current + incoming_cost - cap
    candidates: list[str] = []
    freed = 0
    for line in live:  # append-only storage ⇒ list order is oldest-first
        if freed >= need:
            break
        candidates.append(line.text)
        freed += len(line.text) + 1
    return TrimProposal(
        slot=name,
        cap_chars=cap,
        current_chars=current,
        incoming_chars=incoming_cost,
        drop_candidates=candidates,
    )


class _SlotStore(Protocol):
    """The slice of the vector store slots need. Narrow on purpose so tests can pass a fake
    without standing up sqlite + FAISS."""

    def get_semantic(self, key: str) -> dict | None: ...

    def set_semantic(
        self, key: str, value: object, confidence: float, source: str
    ) -> tuple[Any, str] | None: ...


def _raw_value(store: _SlotStore, name: str) -> Any:
    row = store.get_semantic(key_for(name))
    if not row:
        return None
    if "value" in row and row.get("value") is not None:
        return row["value"]
    try:
        return json.loads(row.get("value_json") or "null")
    except (json.JSONDecodeError, TypeError):
        return None


def is_materialized(store: _SlotStore, name: str) -> bool:
    """Whether a row exists for this slot yet. False for every built-in on a fresh install."""
    return store.get_semantic(key_for(name)) is not None


def load(store: _SlotStore, name: str) -> list[SlotLine]:
    """Every line in a slot, tombstones included. ``[]`` when not materialized."""
    return parse_lines(_raw_value(store, name))


def append(
    store: _SlotStore,
    name: str,
    text: str,
    *,
    source: str = "user_explicit",
    reinforce: bool = False,
) -> list[SlotLine]:
    """Append one line to a slot. Append-only, cap-enforced, tombstone-respecting.

    Raises :class:`SlotCapExceeded` (carrying a :class:`TrimProposal`) when the line would
    breach the cap — nothing is written in that case. Returns the slot's lines after the write.

    A line whose text a HUMAN tombstoned is never re-added: with *reinforce* the observation is
    dropped entirely, without it the call is a no-op that returns the existing lines. An
    already-live duplicate is not appended twice either; with *reinforce* it bumps that line's
    ``reinforcements`` instead, which is how a reflection pass accumulates evidence without
    growing the slot."""
    line_text = text.strip()
    if not line_text:
        return load(store, name)
    existing = load(store, name)

    for line in existing:
        if line.text != line_text:
            continue
        if line.tombstoned:
            if line.tombstoned_by == "human":
                # The resurrection guard. Deliberately silent-but-recorded: the caller asked
                # for something the user already refused, and re-raising would turn every
                # reflection pass into an error the user cannot act on.
                return existing
            break
        if reinforce:
            bumped = [
                (
                    SlotLine(
                        text=line.text,
                        added_at=line.added_at,
                        tombstoned=line.tombstoned,
                        tombstoned_by=line.tombstoned_by,
                        reinforcements=line.reinforcements + 1,
                    )
                    if candidate.text == line_text and not candidate.tombstoned
                    else candidate
                )
                for candidate in existing
            ]
            store.set_semantic(key_for(name), to_value(bumped), 1.0, source)
            return bumped
        return existing

    proposal = propose_trim(name, existing, line_text)
    if proposal.over_by > 0:
        raise SlotCapExceeded(proposal)

    appended = existing + [SlotLine(text=line_text, added_at=_now_iso())]
    result = store.set_semantic(key_for(name), to_value(appended), 1.0, source)
    if result is not None:
        # The store refused (allowlist, injection, size). Surface it rather than returning a
        # list that implies the write landed.
        code, reason = result
        raise SlotCapExceeded(
            TrimProposal(
                slot=name,
                cap_chars=cap_for(name),
                current_chars=live_chars(to_value(existing)),
                incoming_chars=len(line_text),
                drop_candidates=[f"store rejected the write ({code}): {reason}"],
            )
        )
    return appended


def tombstone(
    store: _SlotStore, name: str, text: str, *, actor: str = "human", source: str = "user_explicit"
) -> bool:
    """Mark a line dead without removing it. Returns False when the line isn't there.

    The row keeps the line so :func:`append` can refuse to resurrect it; *actor* is what makes
    that refusal final for a human deletion."""
    line_text = text.strip()
    existing = load(store, name)
    found = False
    updated: list[SlotLine] = []
    for line in existing:
        if line.text == line_text and not line.tombstoned:
            found = True
            updated.append(
                SlotLine(
                    text=line.text,
                    added_at=line.added_at,
                    tombstoned=True,
                    tombstoned_by=actor,
                    reinforcements=line.reinforcements,
                )
            )
        else:
            updated.append(line)
    if not found:
        return False
    store.set_semantic(key_for(name), to_value(updated), 1.0, source)
    return True


def over_cap(store: _SlotStore, names: list[str] | None = None) -> dict[str, int]:
    """Slots currently over their cap, and by how much. ``{}`` when all are within bounds.

    The caps are structural for anything going through :func:`append`, but a row edited by hand
    can say anything, and an over-cap slot would blow the block budget it must fit."""
    check = names if names is not None else list(BUILTIN_SLOTS)
    out: dict[str, int] = {}
    for name in check:
        if not is_materialized(store, name):
            continue
        excess = live_chars(_raw_value(store, name)) - cap_for(name)
        if excess > 0:
            out[name] = excess
    return out


def render_slots_block(
    store: _SlotStore,
    *,
    names: list[str] | None = None,
    limit: int = SLOTS_BLOCK_MAX_CHARS,
) -> str:
    """The ONE Slots block for the session context, hard-bounded at *limit* characters.

    Returns ``""`` when no slot is materialized, so a fresh install pays nothing. The final
    slice is unconditional: a hand-edited row over its per-slot cap still cannot make this
    block exceed *limit*, which is the property :func:`over_cap` alone would not give."""
    order = names if names is not None else list(BLOCK_ORDER)
    sections: list[str] = []
    for name in order:
        if not is_materialized(store, name):
            continue  # lazy: never render a header for a slot nobody wrote
        live = live_lines(parse_lines(_raw_value(store, name)))
        if not live:
            continue
        spec = spec_for(name)
        body = "\n".join(f"- {line.text}" for line in live)
        sections.append(f"{spec.title}:\n{body}")
    if not sections:
        return ""
    block = "[MEMORY SLOTS]\n" + "\n".join(sections) + "\n"
    if len(block) > limit:
        keep = max(0, limit - len(_TRUNCATION_MARKER))
        block = block[:keep] + _TRUNCATION_MARKER
    return block
