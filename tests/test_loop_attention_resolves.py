"""A loop that stops waiting on the user closes its inbox row (issue 335).

The watchdog raises a durable "Loop blocked — needs you" row for a waiting loop. Nothing ever
closed it. Measured on `origin/main` by executing the real paths:

    emit_attention_item(..., refs={"loop": L}, dedup_key=f"loop:{L}:blocked")
        -> 1 PENDING row, 1 notification
    attention.resolve_gate_item(state, L)      -> closed 0     (it matches refs["workflow"])
    re-emit, same key                          -> the SAME row, and NO second notification

So both halves of the reported failure reproduce, and they compound: a standing demand the user
cannot clear by resolving its cause, plus a real later block that is never delivered — because
`emit_attention_item` returns an open row untouched and fires nothing, and the loop key
`loop:<id>:<event>` is permanent per (loop, event) rather than occurrence-scoped like the
workflow key.

Note the third measurement above: the resolver the issue suggested reusing **cannot** close a
loop row. `resolve_gate_item` hard-coded `refs["workflow"]`, so the mechanism existed but was
reachable only for workflows — which is why `workflows/attention.py`'s own docstring can say
"the same seam the loop watchdog uses" and still leave the loop with no resolver.

The fix puts the resolve where the emit already lives (`inbox.resolve_attention_items`, matching
on a ref subset so each caller supplies its own vocabulary), and fires it from the one place that
knows what status a loop is coming FROM — `loop.store.update_status`, on the ATTENTION →
non-ATTENTION transition, derived from `LOOP_PHASES` so a new attention status inherits it.

ARCC was queried first (user-facing notification records). It returned no local-store guidance —
its notification material is AWS infrastructure (SNS/CloudWatch/EventBridge). Two of its SAX-08
outcome-2 pitfalls do name this defect's mechanism and are applied: *alert fatigue from warnings
the user cannot action*, and *failing to deliver an alert channel's later occurrences*.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest

from personalclaw import inbox as inbox_mod
from personalclaw.inbox import InboxStore, ItemKind, ItemStatus, emit_attention_item
from personalclaw.loop import store as loop_store
from personalclaw.loop.loop import ATTENTION_STATUSES, LOOP_PHASES, Loop, LoopStatus
from personalclaw.workflows import attention


class _Svc:
    def __init__(self, store: InboxStore) -> None:
        self.inbox = store


class _State:
    """Shaped as `inbox.live_store` requires: `_inbox_svc.inbox` IS a real `InboxStore`.

    Deliberately not a `MagicMock` — `live_store` is isinstance-checked precisely because a mock
    answers every getattr, which would route the writes into the fake and pass vacuously.
    """

    def __init__(self, store: InboxStore) -> None:
        self._inbox_svc = _Svc(store)
        self.notified: list[tuple] = []

    def notify(self, *args: Any, **kwargs: Any) -> None:
        self.notified.append(args)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path, raising=False)
    return tmp_path


@pytest.fixture
def live(home: Path, monkeypatch: pytest.MonkeyPatch) -> _State:
    """A registered process-wide state, as the running gateway has.

    `store.update_status` reaches it through `native_source.get_dashboard_state()`, so without
    this the resolve would fall back to a disk-backed copy and the assertions would be reading a
    different object than the one being written.
    """
    store = InboxStore()
    store.load()
    state = _State(store)
    from personalclaw.inbox_providers import native_source

    monkeypatch.setattr(native_source, "_dashboard_state", state, raising=False)
    return state


def _loop(status: LoopStatus = LoopStatus.RUNNING) -> Loop:
    loop = loop_store.create(Loop(id="", kind="code", name="bell_times tier gap", task="t"))
    if status is not LoopStatus.READY:
        loop_store.update_status(loop.id, status)
    return loop


def _raise_row(state: _State, loop_id: str, *, event: str = "blocked", cycles: int = 0) -> str:
    """The watchdog's real emit, with its real arguments."""
    return emit_attention_item(
        state,
        source="loop",
        kind="needs_input",
        item_kind=ItemKind.NEEDS_INPUT.value,
        title="Loop blocked — needs you",
        body="bell_times tier gap",
        refs={"loop": loop_id, "loop_kind": "code"},
        dedup_key=f"loop:{loop_id}:{event}:{cycles}",
    )


# ── the defect ───────────────────────────────────────────────────────────────────────────


class TestResume:
    def test_a_resumed_loop_closes_its_row(self, live: _State) -> None:
        """🔑 The reported bug. `manager.start` (used for BOTH start and resume) transitions to
        RUNNING; the row survived it forever, for a loop that was already running."""
        loop = _loop(LoopStatus.BLOCKED)
        item = _raise_row(live, loop.id)
        assert live._inbox_svc.inbox.items[item].status == ItemStatus.PENDING.value

        loop_store.update_status(loop.id, LoopStatus.RUNNING)

        assert live._inbox_svc.inbox.items[item].status == ItemStatus.HANDLED.value

    def test_handled_not_dismissed(self, live: _State) -> None:
        """The engine answered on the user's behalf; "dismissed" would record that they ignored
        it, which is a different fact and feeds the engagement signals differently."""
        loop = _loop(LoopStatus.BLOCKED)
        item = _raise_row(live, loop.id)
        loop_store.update_status(loop.id, LoopStatus.RUNNING)
        assert live._inbox_svc.inbox.items[item].status != ItemStatus.DISMISSED.value

    def test_the_next_real_block_is_delivered_again(self, live: _State) -> None:
        """🔑 The second-order half. `emit_attention_item` returns an OPEN row untouched and
        fires no notification, so before the resolve existed one block per loop was all the user
        would ever hear about for the lifetime of the home."""
        loop = _loop(LoopStatus.BLOCKED)
        first = _raise_row(live, loop.id)
        assert len(live.notified) == 1

        loop_store.update_status(loop.id, LoopStatus.RUNNING)
        loop_store.update_status(loop.id, LoopStatus.BLOCKED)
        second = _raise_row(live, loop.id)

        assert second != first, "the later block was swallowed by the dedup key"
        assert len(live.notified) == 2, "a new row was filed with no notification"

    def test_a_paused_loop_KEEPS_its_row(self, live: _State) -> None:
        """🪤 The deliberate boundary, and the one case that must not close. PAUSED is an
        ATTENTION phase: the user chose to defer the question rather than answer it, so the
        question is still theirs and the row is what reminds them. A fix that closed on every
        transition out of BLOCKED would silently drop it here."""
        loop = _loop(LoopStatus.BLOCKED)
        item = _raise_row(live, loop.id)
        loop_store.update_status(loop.id, LoopStatus.PAUSED)
        assert live._inbox_svc.inbox.items[item].status == ItemStatus.PENDING.value

    @pytest.mark.parametrize("ending", [LoopStatus.COMPLETE, LoopStatus.FAILED, LoopStatus.STOPPED])
    def test_an_ended_loop_closes_its_row(self, live: _State, ending: LoopStatus) -> None:
        """A finished loop answers its own question by ending — nothing about it is actionable.
        Same reasoning as `resolve_run_items` for a cancelled workflow run."""
        loop = _loop(LoopStatus.BLOCKED)
        item = _raise_row(live, loop.id)
        loop_store.update_status(loop.id, ending)
        assert live._inbox_svc.inbox.items[item].status == ItemStatus.HANDLED.value


class TestTheRailIsDerived:
    """The ratchet: the rule is read off `LOOP_PHASES`, not a list in the fix."""

    @pytest.mark.parametrize(
        "source,target",
        [
            (s, t)
            for s, t in itertools.product(
                sorted(ATTENTION_STATUSES, key=lambda x: x.value),
                sorted(LOOP_PHASES, key=lambda x: x.value),
            )
            if t not in ATTENTION_STATUSES
        ],
    )
    def test_every_attention_status_resolves_on_every_way_out(
        self, live: _State, source: LoopStatus, target: LoopStatus
    ) -> None:
        """Enumerated from the phase map, so a NEW attention status (or a new non-attention one)
        is covered the day it is added rather than the day someone remembers to add a case."""
        loop = _loop(source)
        item = _raise_row(live, loop.id)
        loop_store.update_status(loop.id, target)
        assert (
            live._inbox_svc.inbox.items[item].status == ItemStatus.HANDLED.value
        ), f"{source.value} -> {target.value} left the row open"

    @pytest.mark.parametrize(
        "source,target",
        [
            (s, t)
            for s, t in itertools.permutations(sorted(ATTENTION_STATUSES, key=lambda x: x.value), 2)
        ],
    )
    def test_an_attention_to_attention_move_keeps_the_row(
        self, live: _State, source: LoopStatus, target: LoopStatus
    ) -> None:
        """The other direction of the same rule: a loop still waiting on the user still has a
        standing request. Without this, a blocked → needs_input escalation would close the row
        the escalation is about."""
        loop = _loop(source)
        item = _raise_row(live, loop.id)
        loop_store.update_status(loop.id, target)
        assert live._inbox_svc.inbox.items[item].status == ItemStatus.PENDING.value

    def test_the_attention_set_is_derived_and_non_empty(self) -> None:
        """Vacuity floor for both rails above: an empty set would make every parametrisation
        collapse to nothing and both tests would "pass" with zero cases."""
        from personalclaw.loop.loop import LifecyclePhase

        assert ATTENTION_STATUSES == frozenset(
            s for s, phase in LOOP_PHASES.items() if phase is LifecyclePhase.ATTENTION
        )
        assert len(ATTENTION_STATUSES) >= 4
        assert LoopStatus.BLOCKED in ATTENTION_STATUSES
        assert LoopStatus.RUNNING not in ATTENTION_STATUSES

    def test_the_resolve_is_reached_from_update_status(self, live: _State, monkeypatch) -> None:
        """🪤 Every new guard needs a non-test caller. Observed through the RESULT rather than a
        source grep, which would pass on an import that is never invoked."""
        loop = _loop(LoopStatus.BLOCKED)
        seen: list[dict] = []
        monkeypatch.setattr(
            inbox_mod, "resolve_attention_items", lambda _state, refs, **_kw: seen.append(refs) or 0
        )
        loop_store.update_status(loop.id, LoopStatus.RUNNING)
        assert seen == [{"loop": loop.id}]

    def test_an_ordinary_transition_does_not_touch_the_inbox(
        self, live: _State, monkeypatch
    ) -> None:
        """The resolve is scoped to the one transition that means something. A status write that
        was never waiting on the user must not pay for an inbox scan."""
        loop = _loop(LoopStatus.RUNNING)
        calls: list[dict] = []
        monkeypatch.setattr(
            inbox_mod,
            "resolve_attention_items",
            lambda _state, refs, **_kw: calls.append(refs) or 0,
        )
        loop_store.update_status(loop.id, LoopStatus.COMPLETE)
        assert calls == []


# ── the generic resolver ─────────────────────────────────────────────────────────────────


class TestResolveAttentionItems:
    def test_matching_is_a_ref_SUBSET_so_one_implementation_serves_every_emitter(
        self, live: _State
    ) -> None:
        store = live._inbox_svc.inbox
        loop_row = _raise_row(live, "L1")
        gate = emit_attention_item(
            live,
            source="loop",
            kind="needs_input",
            item_kind=ItemKind.NEEDS_INPUT.value,
            title="Approve?",
            refs={"workflow": "r1", "workflow_node": "n1"},
            dedup_key="workflow:r1:n1:0",
        )
        assert inbox_mod.resolve_attention_items(live, {"loop": "L1"}) == 1
        assert store.items[loop_row].status == ItemStatus.HANDLED.value
        assert store.items[gate].status == ItemStatus.PENDING.value, "a loop resolve hit a gate row"

    def test_every_pair_must_match_so_a_scoped_resolve_cannot_close_a_sibling(
        self, live: _State
    ) -> None:
        store = live._inbox_svc.inbox
        n1 = emit_attention_item(
            live,
            source="loop",
            kind="needs_input",
            item_kind=ItemKind.NEEDS_INPUT.value,
            title="a",
            refs={"workflow": "r1", "workflow_node": "n1"},
            dedup_key="k1",
        )
        n2 = emit_attention_item(
            live,
            source="loop",
            kind="needs_input",
            item_kind=ItemKind.NEEDS_INPUT.value,
            title="b",
            refs={"workflow": "r1", "workflow_node": "n2"},
            dedup_key="k2",
        )
        assert (
            inbox_mod.resolve_attention_items(live, {"workflow": "r1", "workflow_node": "n1"}) == 1
        )
        assert store.items[n1].status == ItemStatus.HANDLED.value
        assert store.items[n2].status == ItemStatus.PENDING.value

    def test_an_empty_ref_dict_closes_NOTHING(self, live: _State) -> None:
        """🪤 The dangerous input: `{}` matches every row, so a caller that computed its refs
        from a missing id would empty the user's inbox."""
        row = _raise_row(live, "L1")
        assert inbox_mod.resolve_attention_items(live, {}) == 0
        assert live._inbox_svc.inbox.items[row].status == ItemStatus.PENDING.value

    def test_a_blank_ref_VALUE_closes_nothing_even_when_a_row_carries_it(
        self, live: _State
    ) -> None:
        """🪤 The case that is genuinely reachable, and the one a weaker test misses.

        The watchdog stamps `loop_kind: loop.kind if loop else ""` — so a row raised while the
        loop record could not be read really does carry a blank ref value. Without the guard,
        resolving `{"loop": X, "loop_kind": ""}` MATCHES that row: every pair compares equal.
        Asserted against such a row on purpose; a fixture whose refs are all non-blank passes
        this whether the guard exists or not (measured — two of my first three parametrisations
        were vacuous for exactly that reason).
        """
        blank_kind = emit_attention_item(
            live,
            source="loop",
            kind="needs_input",
            item_kind=ItemKind.NEEDS_INPUT.value,
            title="raised while the loop record was unreadable",
            refs={"loop": "L1", "loop_kind": ""},
            dedup_key="k-blankkind",
        )
        assert inbox_mod.resolve_attention_items(live, {"loop": "L1", "loop_kind": ""}) == 0
        assert live._inbox_svc.inbox.items[blank_kind].status == ItemStatus.PENDING.value
        # …and the row is still resolvable the correct way, by the ref that identifies it.
        assert inbox_mod.resolve_attention_items(live, {"loop": "L1"}) == 1

    @pytest.mark.parametrize(
        "settled", [ItemStatus.DISMISSED.value, ItemStatus.HANDLED.value, ItemStatus.SENT.value]
    )
    def test_a_settled_row_is_not_rewritten(self, live: _State, settled: str) -> None:
        """The user's own action wins. Rewriting a row they dismissed would resurrect a decision
        they already made — and `SENT` belongs to the reply machinery, not to attention."""
        row = _raise_row(live, "L1")
        live._inbox_svc.inbox.items[row].status = settled
        assert inbox_mod.resolve_attention_items(live, {"loop": "L1"}) == 0
        assert live._inbox_svc.inbox.items[row].status == settled

    def test_it_is_best_effort_and_never_raises(self, live: _State, monkeypatch) -> None:
        """A loop transition must not fail because the inbox could not be written."""
        _raise_row(live, "L1")

        def _boom(*_a, **_k):
            raise OSError("disk gone")

        monkeypatch.setattr(InboxStore, "save", _boom)
        assert inbox_mod.resolve_attention_items(live, {"loop": "L1"}) == 0

    def test_a_loop_transition_survives_an_unwritable_inbox(
        self, live: _State, monkeypatch
    ) -> None:
        """The same rule one layer up, asserted where it matters: the loop still transitions."""
        loop = _loop(LoopStatus.BLOCKED)
        _raise_row(live, loop.id)
        monkeypatch.setattr(
            inbox_mod,
            "resolve_attention_items",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")),
        )
        assert loop_store.update_status(loop.id, LoopStatus.RUNNING).status == "running"


class TestOneOpenStatusVocabulary:
    def test_resolution_and_re_arming_read_the_same_set(self, live: _State) -> None:
        """🪤 The invariant that makes the two halves of this fix one fix.

        `_find_open_by_dedup` SUPPRESSES while a row is open; the resolver CLOSES what is open.
        If those sets diverged, a row could be "resolved" and still swallow the next occurrence —
        exactly the compounding failure, arrived at from the other side. Asserted behaviourally
        over every status: for each one, "the dedup finds it" and "the resolver closes it" are
        the same answer.
        """
        store = live._inbox_svc.inbox
        for status in (s.value for s in ItemStatus):
            store.items.clear()
            row = _raise_row(live, "L1")
            store.items[row].status = status
            suppresses = (
                inbox_mod._find_open_by_dedup(store, store.items[row].refs["dedup_key"]) is not None
            )
            store.items[row].status = status  # _find_open_by_dedup does not mutate; be explicit
            closes = inbox_mod.resolve_attention_items(live, {"loop": "L1"}) == 1
            assert suppresses == closes, f"{status}: suppression and resolution disagree"

    def test_the_workflow_module_no_longer_keeps_its_own_copy(self) -> None:
        """The clean break. A second definition is how the two would drift; the workflow module
        now supplies only its ref vocabulary."""
        assert not hasattr(attention, "_OPEN_STATUSES")
        assert inbox_mod.OPEN_STATUSES == frozenset(
            {ItemStatus.PENDING.value, ItemStatus.SEEN.value}
        )


class TestTheWorkflowPathStillWorks:
    """Regression cover for the delegation — the gate path is the caller that already worked."""

    def test_a_gate_resolve_is_scoped_by_node(self, live: _State) -> None:
        store = live._inbox_svc.inbox
        n1 = emit_attention_item(
            live,
            source="loop",
            kind="needs_input",
            item_kind=ItemKind.NEEDS_INPUT.value,
            title="a",
            refs={"workflow": "r1", "workflow_node": "n1"},
            dedup_key="k1",
        )
        n2 = emit_attention_item(
            live,
            source="loop",
            kind="needs_input",
            item_kind=ItemKind.NEEDS_INPUT.value,
            title="b",
            refs={"workflow": "r1", "workflow_node": "n2"},
            dedup_key="k2",
        )
        assert attention.resolve_gate_item(live, "r1", "n1") == 1
        assert store.items[n1].status == ItemStatus.HANDLED.value
        assert store.items[n2].status == ItemStatus.PENDING.value
        assert attention.resolve_run_items(live, "r1") == 1
        assert store.items[n2].status == ItemStatus.HANDLED.value

    def test_a_gate_resolve_cannot_be_unscoped_by_an_empty_run_id(self, live: _State) -> None:
        """A small hardening inherited from the generic guard, stated at its measured size.

        Executed against `origin/main`: `resolve_run_items(state, "")` closed exactly ONE row —
        the one whose `refs["workflow"]` was itself `""`. Real workflow and loop rows were
        untouched, because `refs.get("workflow") != ""` is true for both. So this was never the
        empty-the-inbox bug an unscoped resolve looks like; it was a lost row for whichever
        caller passed a blank id. The guard now refuses the call outright, which is the same
        answer for a better reason.
        """
        row = _raise_row(live, "L1")
        blank = emit_attention_item(
            live,
            source="loop",
            kind="needs_input",
            item_kind=ItemKind.NEEDS_INPUT.value,
            title="blank",
            refs={"workflow": ""},
            dedup_key="k-blank",
        )
        assert attention.resolve_run_items(live, "") == 0
        assert live._inbox_svc.inbox.items[row].status == ItemStatus.PENDING.value
        assert live._inbox_svc.inbox.items[blank].status == ItemStatus.PENDING.value


# ── the dedup key ────────────────────────────────────────────────────────────────────────


class TestDedupKey:
    def test_two_polls_of_one_wait_share_a_key(self, home: Path, monkeypatch) -> None:
        """The property that must survive: the watchdog re-observes a blocked loop every tick,
        and each tick must not stack a row. A loop completes no cycles while it waits, so the
        cycle count is what makes "the same wait" and "a later wait" distinguishable."""
        from personalclaw.loop import files as loop_files
        from personalclaw.loop.watchdog import LoopWatchdog

        monkeypatch.setattr(loop_files, "cycles_completed", lambda _id: 8)
        wd = LoopWatchdog.__new__(LoopWatchdog)
        assert wd._attention_dedup_key("L1", "blocked") == wd._attention_dedup_key("L1", "blocked")
        assert wd._attention_dedup_key("L1", "blocked") == "loop:L1:blocked:8"

    def test_a_later_wait_gets_a_different_key(self, home: Path, monkeypatch) -> None:
        from personalclaw.loop import files as loop_files
        from personalclaw.loop.watchdog import LoopWatchdog

        wd = LoopWatchdog.__new__(LoopWatchdog)
        monkeypatch.setattr(loop_files, "cycles_completed", lambda _id: 8)
        first = wd._attention_dedup_key("L1", "blocked")
        monkeypatch.setattr(loop_files, "cycles_completed", lambda _id: 12)
        assert wd._attention_dedup_key("L1", "blocked") != first

    def test_each_event_keeps_its_own_key(self, home: Path, monkeypatch) -> None:
        """`blocked` and `stagnant` are different requests; they were separately keyed before and
        must stay so, or a stalled loop would be silenced by an earlier block."""
        from personalclaw.loop import files as loop_files
        from personalclaw.loop.watchdog import LoopWatchdog

        monkeypatch.setattr(loop_files, "cycles_completed", lambda _id: 3)
        wd = LoopWatchdog.__new__(LoopWatchdog)
        keys = {wd._attention_dedup_key("L1", e) for e in ("blocked", "stagnant", "needs_input")}
        assert len(keys) == 3

    def test_an_unreadable_cycle_count_degrades_to_the_old_key(
        self, home: Path, monkeypatch
    ) -> None:
        """🪤 Fail-soft in the safe direction. Raising inside a publish path would cost the
        notification entirely; the un-suffixed key is the OLD behaviour — still deduped per
        (loop, event), just not self-re-arming — and it cannot double-notify."""
        from personalclaw.loop import files as loop_files
        from personalclaw.loop.watchdog import LoopWatchdog

        def _boom(_id):
            raise OSError("ledger unreadable")

        monkeypatch.setattr(loop_files, "cycles_completed", _boom)
        wd = LoopWatchdog.__new__(LoopWatchdog)
        assert wd._attention_dedup_key("L1", "blocked") == "loop:L1:blocked"

    def test_a_blocked_loop_completes_no_cycles(self, home: Path) -> None:
        """The assumption the key rests on, asserted against the real projection rather than
        trusted: if a waiting loop could complete cycles, the key would change under a re-poll
        and the watchdog would stack a row per tick."""
        from personalclaw.loop import files as loop_files

        loop = _loop(LoopStatus.BLOCKED)
        before = loop_files.cycles_completed(loop.id)
        loop_store.update_status(loop.id, LoopStatus.BLOCKED)
        assert loop_files.cycles_completed(loop.id) == before
