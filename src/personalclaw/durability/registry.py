"""The versioned sync registry — ``registry.json`` (DURABILITY-AND-SYNC §4.1, DAS-6c-ii-a).

One small shared object at the sync root records, per machine, the high-water mark of
what that machine has published::

    {"machines": {"<machine_id>": {"seq": 7, "last_export_at": "…", "manifest_sha": "…"}}}

It is the coordination point the whole sync cycle turns on, and it is deliberately the
ONLY mutable shared object: every shard lives at an insert-only, seq-numbered key
(``machines/<id>/seq-NNNN/…``) that is never rewritten, so the registry is what a machine
compare-and-swaps to announce "I published seq N" and what every other machine reads to
learn "which peers advanced, and to what seq — so which shard prefixes are new to me".

This module is the PURE registry MODEL — parse, serialize (canonically, so the sha a CAS
compares is stable), bump the local machine's seq on a fresh export, compute a peer's new
shard prefixes, and diff two registries to see who moved. It performs NO I/O and knows
nothing of a transport: the CAS write itself is
:meth:`sync_transports.base.SyncTransportProvider.cas_registry`, and the retry loop that
composes this model with that transport is DAS-6c-ii-b. Keeping the model I/O-free is what
makes the CAS contract testable without a remote and keeps a lost-race retry free — the
same inputs always canonicalize to the same bytes and the same sha.
"""

from __future__ import annotations

import hashlib
import json
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
    last_export_at: str = ""  # ISO-8601 UTC; provided by the caller (model is clock-free)
    manifest_sha: str = ""  # sha of the seq's export manifest, for a cheap change probe

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "last_export_at": self.last_export_at,
            "manifest_sha": self.manifest_sha,
        }

    @classmethod
    def from_dict(cls, machine_id: str, d: dict) -> MachineEntry:
        return cls(
            machine_id=machine_id,
            # A corrupt/absent seq degrades to 0 (re-publish), never crashes a sync.
            seq=_int_or_zero(d.get("seq")),
            last_export_at=str(d.get("last_export_at", "") or ""),
            manifest_sha=str(d.get("manifest_sha", "") or ""),
        )


@dataclass
class Registry:
    """The parsed ``registry.json`` — every machine's high-water mark, keyed by id, plus the
    per-entity-family ancestor shas conflict detection compares against (§4.2, DAS-7).

    ``ancestors`` maps ``entry id → {entity id → the content sha both sides last agreed on}``.
    It lives in the SHARED registry on purpose: a common ancestor is by definition common
    knowledge, so "did both sides edit since we agreed?" is only answerable from an object
    both machines read. It is written by the same CAS bump that announces a seq, so a machine
    publishes "here is the state I merged to" and its peer's next pull compares against it.
    """

    machines: dict[str, MachineEntry] = field(default_factory=dict)
    ancestors: dict[str, dict[str, str]] = field(default_factory=dict)

    # ── parse / serialize ────────────────────────────────────────────────────
    @classmethod
    def empty(cls) -> Registry:
        return cls(machines={})

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
        anc_raw = obj.get("ancestors", {}) if isinstance(obj, dict) else {}
        ancestors: dict[str, dict[str, str]] = {}
        for entry_id, rows in (anc_raw if isinstance(anc_raw, dict) else {}).items():
            if not isinstance(rows, dict):
                continue  # a corrupt family degrades to "no ancestry", never crashes a sync
            ancestors[str(entry_id)] = {str(k): str(v) for k, v in rows.items() if v}
        return cls(machines=machines, ancestors=ancestors)

    def to_bytes(self) -> bytes:
        """Serialize canonically (sorted keys, compact) so two machines writing the
        same logical registry produce byte-identical output — the property a CAS sha
        comparison depends on."""
        obj = {
            "machines": {mid: e.to_dict() for mid, e in self.machines.items()},
            "ancestors": {eid: dict(rows) for eid, rows in self.ancestors.items()},
        }
        return canonical_json(obj).encode("utf-8")

    def sha(self) -> str:
        """The sha of the canonical bytes — the ``expected_sha`` a CAS write compares
        against, and a cheap equality check between two registry states."""
        return hashlib.sha256(self.to_bytes()).hexdigest()

    # ── the local machine's high-water mark ──────────────────────────────────
    def seq_of(self, machine_id: str) -> int:
        e = self.machines.get(machine_id)
        return e.seq if e else 0

    def bump(self, machine_id: str, *, manifest_sha: str, now: str) -> int:
        """Advance ``machine_id``'s seq by one on a fresh local export, recording the new
        export's manifest sha and timestamp. Returns the new seq (the one whose
        :func:`shard_prefix` the caller writes the export under). ``now`` is passed in —
        the model never reads the clock, so a replay is deterministic.

        The bump is monotonic: seq only ever increases, so a stale in-memory registry
        (from a lost CAS race, before the caller re-pulls) can never lower the mark.
        """
        cur = self.machines.get(machine_id)
        new_seq = (cur.seq if cur else 0) + 1
        self.machines[machine_id] = MachineEntry(
            machine_id=machine_id,
            seq=new_seq,
            last_export_at=now,
            manifest_sha=manifest_sha,
        )
        return new_seq

    # ── ancestor shas (§4.2 conflict detection) ──────────────────────────────
    def ancestors_for(self, entry_id: str) -> dict[str, str]:
        """The ``entity id → agreed content sha`` map for one entry family. A copy, and
        empty for a family nobody has agreed on yet (which detection reads as "no common
        ancestor" — a deterministic merge, never a conflict)."""
        return dict(self.ancestors.get(entry_id, {}))

    def record_ancestors(self, entry_id: str, shas: dict[str, str]) -> None:
        """Record the shas of an entry family's rows as the new common ancestors.

        Called after a clean reconcile with the rows this machine merged TO — the state it
        is about to publish, i.e. what the next divergence is measured from. Ids under an
        unresolved conflict are deliberately excluded by the caller, so their older ancestor
        survives and the conflict keeps re-detecting instead of self-resolving.
        """
        if not shas:
            return
        cur = self.ancestors.setdefault(entry_id, {})
        for rid, sha in shas.items():
            if rid and sha:
                cur[str(rid)] = str(sha)

    # ── peer discovery ───────────────────────────────────────────────────────
    def peers(self, self_id: str) -> list[MachineEntry]:
        """Every machine other than ``self_id``, seq-descending then id — a stable order
        for the cycle to iterate and for tests to assert."""
        others = [e for mid, e in self.machines.items() if mid != self_id]
        return sorted(others, key=lambda e: (-e.seq, e.machine_id))

    def new_prefixes_since(self, self_id: str, seen: dict[str, int]) -> list[str]:
        """The shard prefixes this machine has NOT yet pulled.

        ``seen`` maps ``peer_id → highest seq already merged from that peer`` (the cursor
        the cycle persists). For every peer whose registry seq exceeds its seen seq, this
        yields one prefix per unseen seq in ascending order — so the cycle pulls exactly
        the shard sets it is missing, oldest first, and applies them in publication order.
        A peer at or below its seen seq contributes nothing (idempotent re-poll).
        """
        out: list[str] = []
        for e in self.peers(self_id):
            already = int(seen.get(e.machine_id, 0) or 0)
            for s in range(already + 1, e.seq + 1):
                out.append(shard_prefix(e.machine_id, s))
        return out

    def advanced_over(self, prior: Registry, *, self_id: str) -> list[MachineEntry]:
        """Peers whose seq is strictly higher than in ``prior`` — used after a re-pull to
        see who moved while we were mid-cycle (so a CAS retry re-merges only fresh work,
        not the whole world). Excludes ``self_id`` (our own bump is not news to us)."""
        out = []
        for e in self.peers(self_id):
            if e.seq > prior.seq_of(e.machine_id):
                out.append(e)
        return out
