"""The versioned sync registry — ``registry.json`` (DAS-6c-ii-a).

One small shared object at the sync root records, per machine, the high-water mark of
what that machine has published::

    {"machines": {"<machine_id>": {"seq": 7, "last_export_at": "…"}}}

It is the coordination point the whole sync cycle turns on, and it is deliberately the
ONLY mutable shared object: every shard lives at an insert-only, seq-numbered key
(``machines/<id>/seq-NNNN/…``) that is never rewritten, so the registry is what a machine
compare-and-swaps to announce "I published seq N" and what every other machine reads to
learn "which peers advanced, and to what seq". Each seq is a whole copy of that machine's
records, so a peer takes only the newest one, and the machine removes the ones a newer copy
replaced (``durability.published``) — the registry names the newest, which is never removed.

It holds nothing of what a copy contains: it is the one object an encrypted sync leaves
readable, so whether an export is new is told by a digest this machine keeps for itself
(``durability.published``), never one written here.

This module is the PURE registry MODEL — parse, serialize (canonically, so the sha a CAS
compares is stable), bump the local machine's seq on a fresh export, and diff two
registries to see who moved. It performs NO I/O and knows
nothing of a transport: the CAS write itself is
:meth:`sync_transports.base.SyncTransportProvider.cas_registry`, and the retry loop that
composes this model with that transport is DAS-6c-ii-b. Keeping the model I/O-free is what
makes the CAS contract testable without a remote and keeps a lost-race retry free — the
same inputs always canonicalize to the same bytes and the same sha.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from personalclaw.durability.shards import canonical_json

#: The registry object's remote-relative key. One per sync root, CAS-guarded.
REGISTRY_KEY = "registry.json"
#: Prefix under which a machine's seq-numbered shard sets live.
_MACHINES_PREFIX = "machines"


def shard_prefix(machine_id: str, seq: int) -> str:
    """The insert-only remote prefix a machine's ``seq`` export is written under.

    ``machines/<machine_id>/seq-NNNN/`` — zero-padded to 4 digits so a lexical
    ``list_remote`` sort is also a chronological one up to seq 9999 (and still merely
    unsorted, never wrong, beyond it). Every shard object key is this prefix + the
    shard's entry-relative path, so a key is never reused and a re-push is a no-op.
    """
    return f"{_MACHINES_PREFIX}/{machine_id}/seq-{seq:04d}/"


def machine_prefix(machine_id: str) -> str:
    """The remote prefix every one of ``machine_id``'s seqs lives under."""
    return f"{_MACHINES_PREFIX}/{machine_id}/"


_SEQ_FOLDER = re.compile(r"seq-(\d{4,})")


def seq_of_key(machine_id: str, key: str) -> int | None:
    """The seq whose copy *key* is an object of, when it is one of ``machine_id``'s: the inverse
    of :func:`shard_prefix`. ``None`` for any other key — another machine's, the registry, or a
    name under this machine's folder that no seq of it wrote."""
    head = machine_prefix(machine_id)
    if not key.startswith(head):
        return None
    folder, sep, rest = key[len(head) :].partition("/")
    match = _SEQ_FOLDER.fullmatch(folder)
    if not sep or not rest or match is None:
        return None
    seq = int(match.group(1))
    # Only the folder `shard_prefix` writes for that seq: `seq-00007` is not seq 7's.
    return seq if shard_prefix(machine_id, seq) == f"{head}{folder}/" else None


def _int_or_zero(v: object) -> int:
    """A registry field that should be an int but might be garbage from a corrupt or
    forward-version registry. Non-integers degrade to 0 (re-publish) rather than crash a
    sync — the coordinator must never die on one machine's bad row."""
    if isinstance(v, bool):  # bool is an int subclass; a stray True should not mean 1
        return 0
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v)
        except ValueError:
            return 0
    return 0


@dataclass
class MachineEntry:
    """One machine's high-water mark in the registry."""

    machine_id: str
    seq: int = 0
    #: When that seq landed, ISO-8601 UTC; provided by the caller (the model is clock-free).
    last_export_at: str = ""

    def to_dict(self) -> dict:
        return {"seq": self.seq, "last_export_at": self.last_export_at}

    @classmethod
    def from_dict(cls, machine_id: str, d: dict) -> MachineEntry:
        return cls(
            machine_id=machine_id,
            # A corrupt/absent seq degrades to 0 (re-publish), never crashes a sync.
            seq=_int_or_zero(d.get("seq")),
            last_export_at=str(d.get("last_export_at", "") or ""),
        )


@dataclass
class Registry:
    """The parsed ``registry.json`` — every machine's high-water mark, keyed by id.

    What each home last agreed on with each peer, record by record, is that home's own
    (:mod:`durability.ancestors`), not the registry's: one map every home wrote held whichever
    agreement was published last, which a third home that had not caught up read as an edit.
    """

    machines: dict[str, MachineEntry] = field(default_factory=dict)
    #: False for the empty registry standing in for a remote that has none yet (:meth:`absent`).
    #: Not part of the registry itself: never serialized, never compared.
    present: bool = field(default=True, repr=False, compare=False)

    # ── parse / serialize ────────────────────────────────────────────────────
    @classmethod
    def empty(cls) -> Registry:
        return cls(machines={})

    @classmethod
    def absent(cls) -> Registry:
        """What a remote with no registry yet holds: an empty registry, marked not ``present``,
        so a swap creates it rather than comparing against bytes that aren't there."""
        return cls(machines={}, present=False)

    @classmethod
    def loads(cls, data: bytes | str | None) -> Registry:
        """Parse registry bytes. ``None``/empty → an empty registry (the remote has no
        registry yet — the first machine to publish CASes from absent). A structurally
        broken registry raises: a mis-parsed coordinator would let two machines both
        believe they own seq N and silently clobber, so this fails loudly instead."""
        if not data:
            return cls.empty()
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        if not text.strip():
            return cls.empty()
        obj = json.loads(text)  # a genuinely corrupt registry SHOULD raise
        machines_raw = obj.get("machines", {}) if isinstance(obj, dict) else {}
        machines = {
            str(mid): MachineEntry.from_dict(str(mid), d)
            for mid, d in machines_raw.items()
            if isinstance(d, dict)
        }
        return cls(machines=machines)

    def to_bytes(self) -> bytes:
        """Serialize canonically (sorted keys, compact) so two machines writing the
        same logical registry produce byte-identical output — the property a CAS sha
        comparison depends on."""
        obj = {"machines": {mid: e.to_dict() for mid, e in self.machines.items()}}
        return canonical_json(obj).encode("utf-8")

    def sha(self) -> str:
        """The sha of the canonical bytes — the ``expected_sha`` a CAS write compares
        against, and a cheap equality check between two registry states."""
        return hashlib.sha256(self.to_bytes()).hexdigest()

    # ── the local machine's high-water mark ──────────────────────────────────
    def seq_of(self, machine_id: str) -> int:
        e = self.machines.get(machine_id)
        return e.seq if e else 0

    def bump(self, machine_id: str, *, now: str) -> int:
        """Advance ``machine_id``'s seq by one on a fresh local export, recording when it
        landed. Returns the new seq (the one whose :func:`shard_prefix` the caller writes the
        export under). ``now`` is passed in — the model never reads the clock, so a replay is
        deterministic.

        The bump is monotonic: seq only ever increases, so a stale in-memory registry
        (from a lost CAS race, before the caller re-pulls) can never lower the mark.
        """
        cur = self.machines.get(machine_id)
        new_seq = (cur.seq if cur else 0) + 1
        self.machines[machine_id] = MachineEntry(
            machine_id=machine_id, seq=new_seq, last_export_at=now
        )
        return new_seq

    # ── peer discovery ───────────────────────────────────────────────────────
    def peers(self, self_id: str) -> list[MachineEntry]:
        """Every machine other than ``self_id``, seq-descending then id — a stable order
        for the cycle to iterate and for tests to assert."""
        others = [e for mid, e in self.machines.items() if mid != self_id]
        return sorted(others, key=lambda e: (-e.seq, e.machine_id))

    def advanced_over(self, prior: Registry, *, self_id: str) -> list[MachineEntry]:
        """Peers whose seq is strictly higher than in ``prior`` — used after a re-pull to
        see who moved while we were mid-cycle (so a CAS retry re-merges only fresh work,
        not the whole world). Excludes ``self_id`` (our own bump is not news to us)."""
        out = []
        for e in self.peers(self_id):
            if e.seq > prior.seq_of(e.machine_id):
                out.append(e)
        return out
