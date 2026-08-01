"""The effect ledger — external side effects get identity, status, and a teardown path.

The journal cache memoizes *outputs* only. Without effect identity, resume/rewind/fork
double-fire external effects — a Slack message sent twice, a task created twice, a VM
provisioned twice. That is the biggest correctness hole in a journaled-replay design
(WF2-R1), and this module is the fix: every side-effecting `action` dispatch records a
typed effect event in `events.jsonl`, keyed by an idempotency key derived from the
execution identity, not the wall clock.

Three rules the controller enforces with what lives here:

* **A committed effect is a boundary.** Re-executing a node whose effect COMMITTED in a
  previous epoch requires an explicit `redo_effects: true` on the node — silently
  re-running it is exactly the double-fire this ledger exists to prevent. (The mutation
  cascade preview that *surfaces* the boundary before a rewind is Slice 4's job; the
  runtime refusal is this slice's.)
* **Teardown before redo.** A provisioning effect that declared a `teardown` command gets
  it run — with the committed output id — before the region re-executes. The BYOI
  contract requires teardown to be idempotent, so running it against an already-gone
  resource is safe.
* **Callers get dedupe.** `workflow_start`/`workflow_edit` accept a caller idempotency
  key with a short-lived cache, so a retried chat tool call returns the existing run id
  instead of minting a second run (the tool surface lands in Slice 6; the cache is
  built and tested here so the seam exists).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shlex
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from personalclaw.workflows import store

logger = logging.getLogger(__name__)


class EffectStatus(str, Enum):
    """One effect's lifecycle. `ATTEMPTED` is written BEFORE dispatch — a crash between
    attempt and outcome must leave evidence that an effect may have fired, because
    "unknown, possibly fired" and "never fired" demand different recovery."""

    ATTEMPTED = "attempted"
    COMMITTED = "committed"
    RETRIED = "retried"
    COMPENSATED = "compensated"
    SKIPPED = "skipped"


def idempotency_key(run_id: str, instance_path: str, epoch: int) -> str:
    """The effect's identity: sha256(run_id + instance_path + epoch).

    Epoch participates so a deliberate re-execution after a rewind gets a NEW key — the
    external system sees a genuinely new request, while a same-epoch retry reuses the
    key and an idempotent receiver can dedupe it.
    """
    raw = f"{run_id}|{instance_path}|{epoch}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class EffectRecord:
    """One journaled effect event, read back from `events.jsonl`."""

    instance_path: str = ""
    idempotency_key: str = ""
    effect_status: EffectStatus = EffectStatus.ATTEMPTED
    epoch: int = 0
    node_id: str = ""
    provider: str = ""
    #: The external resource's own id, pulled from the provider's JSON output — what a
    #: teardown command receives so it can find the thing to tear down.
    output_id: str = ""
    #: The paired teardown command, captured at commit time. Captured rather than
    #: re-read from the spec, because a later spec edit must not change what tears down
    #: an ALREADY-provisioned resource.
    compensation_ref: str = ""

    @classmethod
    def from_event(cls, rec: dict[str, Any]) -> EffectRecord:
        raw = str(rec.get("effect_status", "attempted") or "attempted")
        try:
            status = EffectStatus(raw)
        except ValueError:
            status = EffectStatus.ATTEMPTED  # tolerant: an unknown status is not fatal
        return cls(
            instance_path=str(rec.get("instance_path", "") or ""),
            idempotency_key=str(rec.get("idempotency_key", "") or ""),
            effect_status=status,
            epoch=int(rec.get("epoch", 0) or 0),
            node_id=str(rec.get("node_id", "") or ""),
            provider=str(rec.get("provider", "") or ""),
            output_id=str(rec.get("output_id", "") or ""),
            compensation_ref=str(rec.get("compensation_ref", "") or ""),
        )


# ── ledger reads ─────────────────────────────────────────────────────────────


def effect_history(run_id: str) -> dict[str, list[EffectRecord]]:
    """Fold the run's effect events into path → ordered records. Order is the journal's
    append order, which is the only order that matters: the LAST record for a path is
    its current standing."""
    from personalclaw.workflows.journal import EFFECT, EVENTS_FILE

    out: dict[str, list[EffectRecord]] = {}
    for rec in store.read_jsonl(run_id, EVENTS_FILE):
        if rec.get("kind") != EFFECT:
            continue
        record = EffectRecord.from_event(rec)
        out.setdefault(record.instance_path, []).append(record)
    return out


def committed_effect(records: list[EffectRecord]) -> EffectRecord | None:
    """The path's standing committed effect, or None.

    A later COMPENSATED for the same key retires the commitment: the resource was torn
    down, so re-execution is no longer a double-fire. Walked newest-first so the most
    recent standing decides.
    """
    compensated: set[str] = set()
    for rec in reversed(records):
        if rec.effect_status == EffectStatus.COMPENSATED:
            compensated.add(rec.idempotency_key)
        elif rec.effect_status == EffectStatus.COMMITTED:
            if rec.idempotency_key in compensated:
                return None
            return rec
    return None


def redo_blocked(node_config: dict[str, Any], committed: EffectRecord | None, epoch: int) -> bool:
    """True when re-execution must be refused (WF2-R1).

    Only a DIFFERENT epoch triggers the gate: a same-epoch retry reuses the same
    idempotency key, which an idempotent receiver dedupes — that is the retry working as
    designed, not a double-fire.
    """
    if committed is None or committed.epoch == epoch:
        return False
    return not bool((node_config or {}).get("redo_effects", False))


# ── BYOI output + teardown ───────────────────────────────────────────────────


def parse_byoi_output(stdout: str) -> dict[str, Any] | None:
    """Enforce the BYOI stdout contract: exactly ONE JSON object.

    Stricter than the engine's loose parser on purpose — a provisioning provider that
    prints two objects is ambiguous about which resource id the teardown should receive,
    and guessing wrong orphans a resource. Ambiguity is a contract violation, not a
    parsing puzzle.
    """
    raw = (stdout or "").strip()
    if not raw:
        return None
    try:
        decoder = json.JSONDecoder()
        obj, end = decoder.raw_decode(raw)
    except ValueError:
        return None
    if raw[end:].strip():
        return None  # trailing content = more than one object = ambiguous
    return obj if isinstance(obj, dict) else None


def output_id_of(output: Any) -> str:
    """The external resource id a teardown receives, from the provider's JSON output."""
    if isinstance(output, dict):
        return str(output.get("id", "") or "")
    return ""


async def run_teardown(
    command: str,
    output_id: str,
    *,
    runner: Any = None,
    timeout: float = 60.0,
) -> tuple[bool, str]:
    """Run the paired teardown, handing it the committed output id.

    The id goes both as the final argv element and as `EFFECT_OUTPUT_ID` in the
    environment — argv for scripts, env for commands whose arg shape is fixed. No shell:
    a teardown command runs against real external resources and must not be a quoting
    injection surface.

    Returns `(ok, detail)`. The BYOI contract requires the command itself to be
    idempotent, so calling it for an already-gone resource must exit 0.
    """
    if runner is not None:
        try:
            return await runner(command, output_id)
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"[:500]
    import os

    argv = shlex.split(command)
    if not argv:
        return False, "empty teardown command"
    argv.append(output_id)
    env = {**os.environ, "EFFECT_OUTPUT_ID": output_id}
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return False, f"teardown timed out after {timeout}s"
    except FileNotFoundError:
        return False, f"teardown command not found: {argv[0]}"
    except OSError as exc:
        return False, f"teardown could not start: {exc}"[:500]
    if proc.returncode == 0:
        return True, (out or b"").decode("utf-8", "replace")[:2000]
    detail = (err or b"").decode("utf-8", "replace")[:2000]
    return False, f"teardown exited {proc.returncode}: {detail}"


# ── caller idempotency dedupe (start/edit) ───────────────────────────────────


@dataclass
class CallerDedupe:
    """Short-lived caller-key → run-id cache (WF2-R1).

    A chat tool call that times out client-side gets retried with the SAME caller key;
    without this cache the retry mints a second run doing the same work. Deliberately
    in-memory and short-TTL: the window it protects is the seconds-scale tool-retry
    window, not cross-restart history — the run store already answers "does this run
    exist" durably.
    """

    ttl_secs: float = 900.0
    clock: Any = time.monotonic
    _entries: dict[str, tuple[str, float]] = field(default_factory=dict)

    def remember(self, caller_key: str, run_id: str) -> None:
        if not caller_key:
            return
        self._sweep()
        self._entries[caller_key] = (run_id, float(self.clock()))

    def lookup(self, caller_key: str) -> str | None:
        if not caller_key:
            return None
        entry = self._entries.get(caller_key)
        if entry is None:
            return None
        run_id, stamped = entry
        if float(self.clock()) - stamped > self.ttl_secs:
            self._entries.pop(caller_key, None)
            return None
        return run_id

    def _sweep(self) -> None:
        now = float(self.clock())
        doomed = [k for k, (_rid, ts) in self._entries.items() if now - ts > self.ttl_secs]
        for k in doomed:
            self._entries.pop(k, None)


#: The process-wide cache the Slice-6 tool surface consults. One instance, because two
#: caches would let a retry land in the one that has not seen the first call.
START_DEDUPE = CallerDedupe()
