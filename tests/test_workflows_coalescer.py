"""Per-observer coalesced delivery (WF2-R11, batch-5).

The claim under test: batching is a TRANSPORT optimization and nothing more. A consumer that
unwraps a batch must see the same event sequence, in the same order, with the same envelopes
it would have seen unbatched — otherwise the fold law from Slice 8a no longer holds and the
saving is paid for in correctness.

The load-bearing behaviours:

* a one-tick fan-out becomes ONE frame, not N;
* pass-through events (anything a human acts on) are never delayed and never reordered
  behind a pending batch;
* the debounce window is bounded by its FIRST event, so a steady stream cannot starve it;
* a terminal run and a shutdown both flush, so nothing is stranded in a window whose timer
  will never fire.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.workflows.coalescer import (
    BATCH_EVENT,
    COALESCING_EVENTS,
    EventCoalescer,
    is_coalescing,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _sink() -> tuple[list[tuple[str, str, object]], object]:
    frames: list[tuple[str, str, object]] = []

    def sink(key: str, event: str, payload: object) -> None:
        frames.append((key, event, payload))

    return frames, sink


def _node(path: str, event: str = "workflow_node_done", **extra) -> dict:
    return {"instance_path": path, "node_id": path.split(".")[-1], **extra}


class TestClassification:
    def test_only_node_chatter_coalesces(self) -> None:
        """An allowlist, deliberately. Wrongly passing through costs one frame; wrongly
        coalescing delays a gate ask, so the default must be the boring one."""
        assert is_coalescing("workflow_node_done")
        assert is_coalescing("workflow_node_started")
        assert is_coalescing("workflow_progress")

    def test_anything_a_human_acts_on_passes_through(self) -> None:
        for event in (
            "workflow_attention",
            "workflow_needs_input",
            "workflow_gate_resolved",
            "workflow_run_update",
            "workflow_spec_updated",
            "workflow_mutation_rejected",
            "workflow_forked",
        ):
            assert not is_coalescing(event), event

    def test_an_unknown_event_defaults_to_pass_through(self) -> None:
        assert not is_coalescing("workflow_something_new_in_slice_12")


class TestBatching:
    async def test_a_fan_out_in_one_tick_becomes_one_frame(self) -> None:
        """The whole point: 20 nodes completing together is ONE write per connection, not
        20 — 20 JSON encodes, 20 socket writes and 20 renders for one logical moment is the
        §9 large-spec widget risk."""
        frames, sink = _sink()
        c = EventCoalescer(sink, window=0.01)
        for i in range(20):
            c.publish("workflow:r1", "workflow_node_done", _node(f"root.children[{i}]"))
        assert frames == []  # nothing written yet — the window is open
        await asyncio.sleep(0.05)
        assert len(frames) == 1
        key, event, payload = frames[0]
        assert (key, event) == ("workflow:r1", BATCH_EVENT)
        assert len(payload["events"]) == 20

    async def test_batch_members_keep_publish_order(self) -> None:
        """Order is the fold's input. A batch that reordered its members would make the
        per-node seq guard fire on legitimate events."""
        frames, sink = _sink()
        c = EventCoalescer(sink, window=0.01)
        for i in range(5):
            c.publish("workflow:r1", "workflow_node_done", _node(f"n{i}", seq=i + 1))
        await asyncio.sleep(0.05)
        seqs = [m["payload"]["seq"] for m in frames[0][2]["events"]]
        assert seqs == [1, 2, 3, 4, 5]

    async def test_each_member_keeps_its_own_envelope(self) -> None:
        """The unbatched-equivalence property: every member still carries the event_id/seq/
        epoch the fold dedups and supersedes on."""
        frames, sink = _sink()
        c = EventCoalescer(sink, window=0.01)
        c.publish(
            "workflow:r1",
            "workflow_node_done",
            {"instance_path": "a", "event_id": "r1-evt-3", "seq": 3, "epoch": 1},
        )
        c.publish(
            "workflow:r1",
            "workflow_node_done",
            {"instance_path": "b", "event_id": "r1-evt-4", "seq": 4, "epoch": 1},
        )
        await asyncio.sleep(0.05)
        members = frames[0][2]["events"]
        assert [m["payload"]["event_id"] for m in members] == ["r1-evt-3", "r1-evt-4"]
        assert all("epoch" in m["payload"] for m in members)

    async def test_a_single_event_is_written_unwrapped(self) -> None:
        """Wrapping the common case would force every consumer to unwrap it; the batch frame
        exists for the many-events case."""
        frames, sink = _sink()
        c = EventCoalescer(sink, window=0.01)
        c.publish("workflow:r1", "workflow_node_done", _node("a"))
        await asyncio.sleep(0.05)
        assert len(frames) == 1
        assert frames[0][1] == "workflow_node_done"

    async def test_a_repeat_for_the_same_instance_supersedes(self) -> None:
        """Two `done`s for one instance inside one window: the later state is strictly more
        current, so keeping both would write a frame that immediately contradicts itself."""
        frames, sink = _sink()
        c = EventCoalescer(sink, window=0.01)
        c.publish("workflow:r1", "workflow_node_done", _node("a", status="running"))
        c.publish("workflow:r1", "workflow_node_done", _node("a", status="done"))
        await asyncio.sleep(0.05)
        assert frames[0][1] == "workflow_node_done"
        assert frames[0][2]["status"] == "done"

    async def test_started_is_not_eaten_by_the_done_that_follows(self) -> None:
        """Keyed by (event, path), not path alone. A node whose `started` was collapsed into
        its `done` would never render as active — the fan-out would look like it teleported
        from pending to finished."""
        frames, sink = _sink()
        c = EventCoalescer(sink, window=0.01)
        c.publish("workflow:r1", "workflow_node_started", _node("a"))
        c.publish("workflow:r1", "workflow_node_done", _node("a"))
        await asyncio.sleep(0.05)
        members = frames[0][2]["events"]
        assert [m["event"] for m in members] == ["workflow_node_started", "workflow_node_done"]

    async def test_a_payload_with_no_instance_path_is_never_collapsed(self) -> None:
        """`workflow_progress` carries a whole node list, not one instance. Collapsing two
        by a missing key would silently drop a tick."""
        frames, sink = _sink()
        c = EventCoalescer(sink, window=0.01)
        c.publish("workflow:r1", "workflow_progress", {"nodes": [], "seq": 1})
        c.publish("workflow:r1", "workflow_progress", {"nodes": [], "seq": 2})
        await asyncio.sleep(0.05)
        assert len(frames[0][2]["events"]) == 2

    async def test_the_batch_flushes_at_the_size_cap(self) -> None:
        """Without a cap, a 500-node fan-out inside one window builds one enormous frame —
        trading many small writes for a single write the client stalls parsing."""
        frames, sink = _sink()
        c = EventCoalescer(sink, window=5.0, max_batch=3)
        for i in range(7):
            c.publish("workflow:r1", "workflow_node_done", _node(f"n{i}"))
        # 3 + 3 flushed immediately; 1 still pending behind the (long) timer.
        assert len(frames) == 2
        assert all(len(f[2]["events"]) == 3 for f in frames)
        assert c.pending == 1


class TestPassThrough:
    async def test_a_gate_ask_is_written_immediately(self) -> None:
        """Delaying an ask by a window to save a frame is a bad trade: it is the one event
        the user is actually waiting on."""
        frames, sink = _sink()
        c = EventCoalescer(sink, window=5.0)
        c.publish("workflow:r1", "workflow_needs_input", {"ask": {"prompt": "ship?"}})
        assert len(frames) == 1
        assert frames[0][1] == "workflow_needs_input"

    async def test_a_pass_through_flushes_the_pending_batch_first(self) -> None:
        """Otherwise a status flip overtakes the node events that logically precede it, and
        a consumer sees the run go `complete` while its last node still reads `running`."""
        frames, sink = _sink()
        c = EventCoalescer(sink, window=5.0)
        c.publish("workflow:r1", "workflow_node_done", _node("a"))
        c.publish("workflow:r1", "workflow_node_done", _node("b"))
        c.publish("workflow:r1", "workflow_run_update", {"status": "complete"})
        assert [f[1] for f in frames] == [BATCH_EVENT, "workflow_run_update"]
        assert c.pending == 0


class TestPerObserver:
    async def test_two_observers_have_independent_windows(self) -> None:
        """A shared window would let one tab's just-flushed timer swallow the other's first
        update, leaving the second tab a full window behind for no reason."""
        frames, sink = _sink()
        c = EventCoalescer(sink, window=0.01)
        c.publish("workflow:r1", "workflow_node_done", _node("a"))
        c.publish("workflow:r2", "workflow_node_done", _node("a"))
        await asyncio.sleep(0.05)
        assert {f[0] for f in frames} == {"workflow:r1", "workflow:r2"}

    async def test_flushing_one_observer_leaves_the_other_pending(self) -> None:
        frames, sink = _sink()
        c = EventCoalescer(sink, window=5.0)
        c.publish("workflow:r1", "workflow_node_done", _node("a"))
        c.publish("workflow:r1", "workflow_node_done", _node("b"))
        c.publish("workflow:r2", "workflow_node_done", _node("a"))
        c.flush("workflow:r1")
        assert [f[0] for f in frames] == ["workflow:r1"]
        assert c.pending == 1


class TestWindowBounds:
    async def test_the_window_is_not_re_armed_by_later_events(self) -> None:
        """The classic debounce-starvation bug: re-arming on every event means a steady
        stream never flushes at all. The FIRST event sets the deadline."""
        frames, sink = _sink()
        c = EventCoalescer(sink, window=0.03)
        for i in range(6):
            c.publish("workflow:r1", "workflow_node_done", _node(f"n{i}"))
            await asyncio.sleep(0.01)
        # ~60ms of steady publishing at a 30ms window: at least one flush must have happened
        # already, which a re-armed timer would have prevented entirely.
        assert frames, "a steady stream starved the debounce window"

    async def test_flush_all_strands_nothing(self) -> None:
        frames, sink = _sink()
        c = EventCoalescer(sink, window=5.0)
        c.publish("workflow:r1", "workflow_node_done", _node("a"))
        c.publish("workflow:r2", "workflow_node_done", _node("a"))
        c.flush_all()
        assert len(frames) == 2
        assert c.pending == 0

    def test_no_running_loop_delivers_immediately(self) -> None:
        """A publish from a worker thread (or a synchronous test) cannot schedule a timer.
        Accumulating there would strand events nothing will ever flush."""
        frames, sink = _sink()
        c = EventCoalescer(sink, window=5.0)
        c.publish("workflow:r1", "workflow_node_done", _node("a"))
        assert len(frames) == 1
        assert c.pending == 0

    async def test_flushing_an_unknown_key_is_a_no_op(self) -> None:
        frames, sink = _sink()
        EventCoalescer(sink).flush("workflow:nope")
        assert frames == []

    async def test_a_broken_sink_never_propagates(self) -> None:
        """A widget's write path is not allowed to take a run down with it."""

        def exploding(key: str, event: str, payload: object) -> None:
            raise RuntimeError("socket gone")

        c = EventCoalescer(exploding, window=0.01)
        c.publish("workflow:r1", "workflow_node_done", _node("a"))
        await asyncio.sleep(0.05)  # the timer fires inside the loop; must not raise


class TestUnbatchedEquivalence:
    async def test_a_batched_stream_replays_to_the_same_sequence(self) -> None:
        """The invariant the FE fold depends on: unwrapping every batch frame yields exactly
        the event sequence a consumer would have received with no coalescer at all."""
        published = [
            ("workflow_node_started", _node("a")),
            ("workflow_node_done", _node("a", status="done")),
            ("workflow_node_started", _node("b")),
            ("workflow_run_update", {"status": "running"}),
            ("workflow_node_done", _node("b", status="done")),
            ("workflow_run_update", {"status": "complete"}),
        ]
        frames, sink = _sink()
        c = EventCoalescer(sink, window=0.01)
        for event, payload in published:
            c.publish("workflow:r1", event, payload)
        c.flush_all()

        replayed: list[tuple[str, object]] = []
        for _key, event, payload in frames:
            if event == BATCH_EVENT:
                replayed.extend((m["event"], m["payload"]) for m in payload["events"])
            else:
                replayed.append((event, payload))
        assert replayed == published


def test_the_allowlist_is_a_subset_of_the_published_events() -> None:
    """A coalescing entry for an event nothing publishes is dead configuration that reads as
    a live guarantee — and the reverse (a typo'd name) would silently never batch."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    published: set[str] = set()
    for rel in (
        "src/personalclaw/workflows/controller.py",
        "src/personalclaw/workflows/service.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        published |= set(re.findall(r'_publish\(\s*"(workflow_[a-z_]+)"', text))
    assert COALESCING_EVENTS <= published, sorted(COALESCING_EVENTS - published)
