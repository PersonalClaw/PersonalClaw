"""The claim store — the three defects that made `overlap` decorative (§3.1 — S97).

**🔴 MEASURED BEFORE WRITING.** `scheduling.claim_fire` decides overlap from an `existing` claim the
caller supplies, and `firepath.evaluate` returns the claim it granted with the note "the caller must
release it". Nobody did either:

1. **`tick()` passed no `existing_claim`**, so every fire was evaluated against `existing=None` and
   the claim gate ALWAYS granted. Driven: a trigger with `overlap: skip` fired a second time while
   its first run was still in flight — the precise failure `overlap` exists to prevent. Present,
   reviewed, enforcing nothing.
2. **Nothing persisted a granted claim**, so `is_running` was unanswerable from the store.
   `ScheduleService` answers it from a PROCESS-LOCAL dict — wrong after a restart (an in-flight
   run reads as idle) and invisible to the MCP process writing the same store. The API facade
   needs it to re-point off `ScheduleService`.
3. **The executor never released one.** Adding (1)+(2) without this would have made things WORSE
   than before: every `overlap: skip` trigger would block itself after one run until the 1h
   expiry, turning the overlap guard into a one-shot. Caught by driving the cycle, not by reading.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from personalclaw.triggers import claims as C
from personalclaw.triggers import executor as E
from personalclaw.triggers import service as SVC
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.scheduling import CLAIM_MAX_DURATION_SECS, Claim
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0


def _claim(tid="j", *, at=NOW, holder="tick", max_secs=CLAIM_MAX_DURATION_SECS):
    return Claim(trigger_id=tid, holder=holder, claimed_at=at, max_duration_secs=max_secs)


def _clock(overlap="skip", tid="j"):
    return Trigger(
        id=tid,
        name="J",
        kind="clock",
        enabled=True,
        overlap=overlap,
        spec={"kind": "interval", "interval_secs": 60},
        workflow={"provider": "run-prompt", "config": {}},
        capabilities={"providers": ["run-prompt"]},
        next_fire_at=SVC.to_iso(NOW - 1),
    )


async def _ok(_payload):
    return {"status": "ok"}


async def _boom(_payload):
    raise RuntimeError("provider exploded")


# ── 🔴 defect 1: overlap was inert ──


def test_overlap_skip_now_blocks_a_second_fire(tmp_path):
    """🔴 THE defect. Driven before the fix: the second fire was GRANTED while the first run's claim
    was live, because the tick never supplied `existing_claim`."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock("skip"))
    first = asyncio.run(SVC.tick(store, now=NOW, base_dir=tmp_path))
    assert [f.trigger.id for f in first.fires] == ["j"]

    trigger = store.get("j").trigger
    trigger.next_fire_at = SVC.to_iso(NOW + 59)
    store.upsert(trigger)
    second = asyncio.run(SVC.tick(store, now=NOW + 60, base_dir=tmp_path))
    assert second.fires == []


def test_a_blocked_fire_writes_a_typed_ledger_row(tmp_path):
    """§7 crit 8's zero-silent-drops: a suppressed fire is a typed row, not an absence."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock("skip"))
    asyncio.run(SVC.tick(store, now=NOW, base_dir=tmp_path))
    trigger = store.get("j").trigger
    trigger.next_fire_at = SVC.to_iso(NOW + 59)
    store.upsert(trigger)
    result = asyncio.run(SVC.tick(store, now=NOW + 60, base_dir=tmp_path))
    assert [r["outcome"] for r in result.ledger_rows] == ["skipped_overlap"]


def test_overlap_parallel_still_allows_concurrency(tmp_path):
    """🔴 The over-blocking direction. `parallel` means the user WANTS concurrent runs; blocking it
    would be the same class of bug in reverse."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock("parallel"))
    asyncio.run(SVC.tick(store, now=NOW, base_dir=tmp_path))
    trigger = store.get("j").trigger
    trigger.next_fire_at = SVC.to_iso(NOW + 59)
    store.upsert(trigger)
    second = asyncio.run(SVC.tick(store, now=NOW + 60, base_dir=tmp_path))
    assert [f.trigger.id for f in second.fires] == ["j"]


# ── 🔴 defect 2: is_running was unanswerable ──


def test_a_granted_claim_is_persisted(tmp_path):
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock())
    asyncio.run(SVC.tick(store, now=NOW, base_dir=tmp_path))
    assert C.is_running("j", now=NOW, base_dir=tmp_path) is True
    assert C.running_since("j", now=NOW, base_dir=tmp_path) == NOW


def test_running_state_is_visible_across_processes(tmp_path):
    """🔴 The reason this is a FILE, not a set. `ScheduleService.is_running` reads a process-local
    dict — wrong after a restart and invisible to the MCP process writing the same store. A fresh
    reader (a new process) must see the same answer."""
    C.write_claim(_claim(), base_dir=tmp_path)
    assert C.is_running("j", now=NOW + 1, base_dir=tmp_path) is True
    # A second "process" reading only the directory sees it too.
    assert C.running_ids(now=NOW + 1, base_dir=tmp_path) == ["j"]


def test_an_idle_trigger_reports_not_running(tmp_path):
    assert C.is_running("nope", now=NOW, base_dir=tmp_path) is False
    assert C.running_since("nope", now=NOW, base_dir=tmp_path) is None
    assert C.running_ids(now=NOW, base_dir=tmp_path) == []


def test_a_tick_dry_run_does_not_write_a_claim(tmp_path):
    """`persist=False` is the doctor's dry run — it must not leave a claim that would then
    block a real fire."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock())
    asyncio.run(SVC.tick(store, now=NOW, persist=False, base_dir=tmp_path))
    assert C.is_running("j", now=NOW, base_dir=tmp_path) is False


# ── 🔴 defect 3: the executor never released ──


def test_a_completed_run_releases_its_claim(tmp_path):
    """🔴 Without this, (1)+(2) make things WORSE: every `overlap: skip` trigger blocks ITSELF after
    one run until the 1h expiry — the overlap guard becomes a one-shot."""
    C.write_claim(_claim(), base_dir=tmp_path)
    asyncio.run(E.run_one({"trigger_id": "j"}, _ok, now=NOW, base_dir=tmp_path))
    assert C.is_running("j", now=NOW, base_dir=tmp_path) is False


def test_a_RAISING_run_also_releases_its_claim(tmp_path):
    """🔴 In a `finally`, because a run that raised still finished occupying the trigger. Releasing
    only on success would strand it on every failure — the worst case, since a failing automation is
    exactly the one a user retries."""
    C.write_claim(_claim(), base_dir=tmp_path)
    outcome = asyncio.run(E.run_one({"trigger_id": "j"}, _boom, now=NOW, base_dir=tmp_path))
    assert outcome.outcome == "failed"
    assert C.is_running("j", now=NOW, base_dir=tmp_path) is False


def test_the_whole_cycle_fires_again_after_a_run_completes(tmp_path):
    """The end-to-end property: fire → claim held → run → released → next slot fires."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock("skip"))
    asyncio.run(SVC.tick(store, now=NOW, base_dir=tmp_path))
    assert C.is_running("j", now=NOW, base_dir=tmp_path) is True
    asyncio.run(E.run_one({"trigger_id": "j"}, _ok, now=NOW, base_dir=tmp_path))
    trigger = store.get("j").trigger
    trigger.next_fire_at = SVC.to_iso(NOW + 59)
    store.upsert(trigger)
    again = asyncio.run(SVC.tick(store, now=NOW + 60, base_dir=tmp_path))
    assert [f.trigger.id for f in again.fires] == ["j"]


def test_a_failed_release_does_not_mask_the_runs_outcome(tmp_path):
    """A claim-store problem is not the run's verdict."""

    def broken(_tid, *, base_dir=None):
        raise OSError("disk gone")

    outcome = asyncio.run(
        E.run_one({"trigger_id": "j"}, _ok, now=NOW, release_claim=broken, base_dir=tmp_path)
    )
    assert outcome.outcome == "ran"


def test_release_can_be_disabled_for_a_caller_that_owns_the_claim(tmp_path):
    C.write_claim(_claim(), base_dir=tmp_path)
    asyncio.run(E.run_one({"trigger_id": "j"}, _ok, now=NOW, release_claim=None, base_dir=tmp_path))
    assert C.is_running("j", now=NOW, base_dir=tmp_path) is True


# ── expiry + robustness ──


def test_an_expired_claim_reads_as_idle(tmp_path):
    """🔴 Read-time expiry, not a swept one: a CRASHED run must not hold its trigger hostage until
    some janitor notices."""
    C.write_claim(_claim(at=NOW), base_dir=tmp_path)
    assert C.is_running("j", now=NOW + 1, base_dir=tmp_path) is True
    assert C.is_running("j", now=NOW + CLAIM_MAX_DURATION_SECS + 1, base_dir=tmp_path) is False


def test_an_expired_claim_does_not_block_a_fire(tmp_path):
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock("skip"))
    C.write_claim(_claim(at=NOW - CLAIM_MAX_DURATION_SECS - 10), base_dir=tmp_path)
    result = asyncio.run(SVC.tick(store, now=NOW, base_dir=tmp_path))
    assert [f.trigger.id for f in result.fires] == ["j"]


def test_a_malformed_claim_reads_as_idle_rather_than_blocking_forever(tmp_path):
    """An unparseable claim that blocked every future fire would be worse than one ignored, and the
    file is on disk for a human either way."""
    path = C._claim_path("j", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert C.is_running("j", now=NOW, base_dir=tmp_path) is False
    path.write_text(json.dumps({"trigger_id": "j"}))  # no claimed_at
    assert C.is_running("j", now=NOW, base_dir=tmp_path) is False


def test_release_is_idempotent(tmp_path):
    """The release runs in a `finally`; a run that failed BEFORE its claim was written must not turn
    its own cleanup into a second error."""
    assert C.release_claim("never-claimed", base_dir=tmp_path) is False
    C.write_claim(_claim(), base_dir=tmp_path)
    assert C.release_claim("j", base_dir=tmp_path) is True
    assert C.release_claim("j", base_dir=tmp_path) is False


def test_the_claim_filename_is_filesystem_safe(tmp_path):
    """A trigger id carries a `:` (`file:my-notes`) — legal on posix, broken on Windows."""
    assert ":" not in C._claim_path("clock:my-job", tmp_path).name
    assert C._claim_path("clock:my-job", tmp_path).parent.name == "trigger-claims"


def test_a_claim_write_is_atomic(tmp_path):
    """tmp→rename: a half-written claim read back as malformed would read as IDLE, and the whole
    point of the record is that a second fire can see the first."""
    C.write_claim(_claim(), base_dir=tmp_path)
    leftovers = list(C._claims_dir(tmp_path).glob("*.tmp"))
    assert leftovers == []


def test_writing_a_none_claim_is_a_noop(tmp_path):
    C.write_claim(None, base_dir=tmp_path)
    assert C.running_ids(now=NOW, base_dir=tmp_path) == []


def test_running_ids_is_sorted(tmp_path):
    """A stable order, so list rows do not appear to move on their own between reads."""
    for tid in ("z-job", "a-job", "m-job"):
        C.write_claim(_claim(tid=tid), base_dir=tmp_path)
    assert C.running_ids(now=NOW, base_dir=tmp_path) == ["a-job", "m-job", "z-job"]


def test_running_ids_omits_expired_claims(tmp_path):
    C.write_claim(_claim(tid="live", at=NOW), base_dir=tmp_path)
    C.write_claim(_claim(tid="dead", at=NOW - CLAIM_MAX_DURATION_SECS - 10), base_dir=tmp_path)
    assert C.running_ids(now=NOW, base_dir=tmp_path) == ["live"]


@pytest.mark.parametrize("overlap", ["skip", "queue"])
def test_every_blocking_overlap_mode_is_enforced(overlap, tmp_path):
    """Derived from the modes `claim_fire` treats as blocking, so a new mode cannot silently
    join the permissive side."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock(overlap))
    asyncio.run(SVC.tick(store, now=NOW, base_dir=tmp_path))
    trigger = store.get("j").trigger
    trigger.next_fire_at = SVC.to_iso(NOW + 59)
    store.upsert(trigger)
    assert asyncio.run(SVC.tick(store, now=NOW + 60, base_dir=tmp_path)).fires == []


def test_the_claim_root_is_derived_from_the_store_not_the_real_home(tmp_path):
    """🔴 A DEFECT I INTRODUCED AND CAUGHT BY RUNNING. The first version defaulted the claim root to
    the active home, so a tick over a `tmp_path` store wrote claims into the real
    `~/.personalclaw/trigger-claims` — 7 files landed there, and leftovers then blocked unrelated
    tests' fires. A claim describes ONE store, so its root is that store's directory."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_clock())
    asyncio.run(SVC.tick(store, now=NOW))  # no explicit base_dir
    assert C._claims_dir(tmp_path).is_dir()
    assert C.is_running("j", now=NOW, base_dir=tmp_path) is True
    assert store.base_dir == tmp_path


def test_a_release_without_a_root_is_a_noop_not_a_real_home_write(tmp_path):
    """Same hazard on the release side: `run_one` must not reach into the user's home when the
    caller did not say which store the claim belongs to."""
    from personalclaw.triggers.executor import _release_claim

    assert _release_claim("j") is False
