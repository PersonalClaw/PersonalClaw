"""Graph maintenance lets the gateway answer while it runs, and says which pass it is in.

🔴 The defect (Backlog: "Graph maintenance freezes the gateway for minutes, and nothing names
it"). A write to the library marks it dirty, and the next graph-maintenance run makes every pass
over the whole library in a gateway thread. Consolidation's planner compares the pooled items
pairwise, and with no stored vectors it falls back to token overlap, which tokenized BOTH items'
whole texts again for every pair: each a run of regular expressions over the whole string, one
stretch of C that holds the interpreter until it ends. The event loop drops the interpreter on
every file stat and socket read and waits behind that stretch to take it back, so on a library of
large items `/api/health` timed out at 20 s, chats stalled and the process sat at 98% CPU for 4 to
10 minutes, with nothing in the log naming the pass. The planner's dedup step (`pre_dedup`) did
the same before it: on a scratch home of 100 items of 1 MB, 4,950 pairs and 10,000 whole-text
tokenizings, 160 s of the pass's 163 s.

Now each item is tokenized once per pass, for the dedup and the floor alike
(`consolidation.token_lookup`), and every normalizing reads its text in bounded steps
(`consolidation.TOKEN_STEP_CHARS`), a word a step's end cuts in two held over to the next, so the
tokens are exactly the whole text's and no call holds the interpreter for more than a moment.
The consolidate action reads and plans in a worker thread, never on the loop that awaits it (a
workflow's, the Knowledge panel's), and the planner rests a moment after every slice of work
(`consolidation.Pace`), so the loop takes the interpreter back at once. Each pass's time is kept
(`MaintenanceResult.per_pass_secs`) and a slow run's log line names it; and the pass a run is in
is what the running list shows while it runs (`running_now`).

Measured as the gateway feels it: an event loop's 1 ms sleeps, each wake taking the interpreter
back, timed while the action plans. On a scratch home of 100 items of 1 MB, `/api/healthz`
answered in 7.9 s at worst while maintenance ran before, and in 0.087 s after (the row's note):
that measurement takes minutes, so the suite pins what made it, in seconds.
"""

from __future__ import annotations

import asyncio
import random
import re
import time

import pytest

from personalclaw.action_providers import knowledge_maintain_provider as kmp
from personalclaw.action_providers.base import ActionContext
from personalclaw.knowledge import consolidation, maintenance
from personalclaw.knowledge.consolidation import Item, cluster_items, plan_consolidation


class _Reads:
    """What the planner hands the normalizing expression: one entry per step it reads."""

    def __init__(self) -> None:
        self.real = consolidation._NOT_KEPT
        self.lengths: list[int] = []

    def sub(self, repl: str, text: str) -> str:
        self.lengths.append(len(text))
        return self.real.sub(repl, text)


def test_each_item_is_tokenized_once_a_pass(monkeypatch):
    """The dedup and the floor share one item's tokens."""
    items = [Item(id=f"k{i}", content=f"alpha{i} beta gamma delta " * 900) for i in range(6)]
    made: list[str] = []
    real = consolidation.tokens_of

    def counting(text: str, **how) -> frozenset[str]:
        made.append(text)
        return real(text, **how)

    monkeypatch.setattr(consolidation, "tokens_of", counting)
    plan_consolidation(items, min_size=2)
    assert sorted(made) == sorted(i.text for i in items), "once per item, not once per pair"


def test_every_read_is_a_bounded_step_and_the_whole_text_is_read(monkeypatch):
    items = [Item(id=f"k{i}", content=f"alpha{i} beta gamma delta " * 9000) for i in range(6)]
    reads = _Reads()
    monkeypatch.setattr(consolidation, "_NOT_KEPT", reads)
    plan_consolidation(items, min_size=2)
    assert max(reads.lengths) <= consolidation.TOKEN_STEP_CHARS
    # Each item read twice in all: once for its dedup hash and once for its tokens.
    assert sum(reads.lengths) == 2 * sum(len(i.text.lower()) for i in items), reads.lengths[:4]


def _whole(text: str) -> str:
    """`normalize_for_dedup` as it read a text, in one call over the whole string."""
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text.lower()).split())


@pytest.mark.parametrize("step", [1, 2, 7, 64, 20_000])
def test_a_text_read_in_steps_is_the_whole_texts(monkeypatch, step):
    """🔴 The planner decides on the whole text's tokens, not on a head of it, and a word a step
    cuts in two is one word: casing, accents, other alphabets, every kind of space, and runs of
    letters longer than a step."""
    rng = random.Random(step)
    alphabet = "abcXYZ019 \t\n\r.,;-_/İKΣé中 \x1c\u00a0" + "q" * 20
    texts = ["", " ", "x", "Ab", "a b", "ΑΣ Σ", "k" * 3 * consolidation.TOKEN_STEP_CHARS]
    texts += [
        "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 400))) for _ in range(200)
    ]
    monkeypatch.setattr(consolidation, "TOKEN_STEP_CHARS", step)
    for text in texts:
        assert consolidation.normalize_for_dedup(text) == _whole(text), repr(text)
        whole = frozenset(re.findall(r"[a-z0-9]{3,}", _whole(text)))
        assert consolidation.tokens_of(text) == whole, repr(text)


def test_the_token_floor_still_groups_what_is_alike():
    alike = [
        Item(id=f"a{i}", content=f"release notes for version {i} of the parser") for i in range(5)
    ]
    other = [
        Item(id=f"b{i}", content=f"grocery list {i}: apples pears milk bread") for i in range(5)
    ]
    # The floor itself: the dedup would fold each five into one, as near-identical.
    clusters = cluster_items(alike + other, min_size=5, threshold=0.3)
    assert sorted(sorted(i.id for i in c.items) for c in clusters) == [
        [f"a{i}" for i in range(5)],
        [f"b{i}" for i in range(5)],
    ]


def test_the_dedup_still_drops_what_says_the_same_thing():
    said = "The Fed held rates steady in March, citing sticky services inflation. " * 50
    items = [Item(id="k1", content=said), Item(id="k2", content=said.upper() + " !!")]
    plan = plan_consolidation(items, min_size=2)
    assert plan.pre_dedup_merges == [("k1", "k2")], plan.pre_dedup_merges


def _many(count: int = 500) -> list[Item]:
    """Many short items, each about something else: the pairwise work of a library of notes."""
    rng = random.Random(11)
    return [
        Item(id=f"m{i}", content=" ".join(f"m{i}w{rng.randrange(600)}" for _ in range(500)))
        for i in range(count)
    ]


def test_the_consolidate_action_leaves_the_loop_that_awaits_it_free(tmp_path, monkeypatch):
    """🔴 Before: a workflow or the Knowledge panel awaits the action on the gateway's own loop,
    and it planned right there, so the loop answered nothing until the plan was made (seconds
    here, minutes on a real library). And a planner in a worker thread that never rested held
    each of the loop's wakes for 140 to 200 ms here, and up to a second on 800 items. Now it plans
    in a worker thread and rests, and a wake waits about 12 ms."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    items = _many()
    monkeypatch.setattr(kmp, "_open_store", lambda: object())
    monkeypatch.setattr(kmp, "_load_items", lambda store: items)
    monkeypatch.setattr(kmp, "_vector_lookup", lambda store: None)
    monkeypatch.setattr(kmp, "_hours_since_last_pass", lambda store: 10_000.0)

    async def awaited_on_this_loop():
        run = asyncio.ensure_future(
            kmp.KnowledgeConsolidateActionProvider().execute(
                {}, ActionContext(event="", context="", payload={})
            )
        )
        worst, wakes = 0.0, 0
        while not run.done():
            woke = time.monotonic()
            await asyncio.sleep(0.001)
            worst, wakes = max(worst, time.monotonic() - woke - 0.001), wakes + 1
        return await run, worst, wakes

    result, worst, wakes = asyncio.run(awaited_on_this_loop())
    assert result.success, result.error
    assert worst < 0.1, f"the loop waited {worst * 1000:.0f} ms at worst, in {wakes} wakes"


def test_a_pace_rests_once_a_slice_is_worked_and_never_on_a_loop():
    now, slept = [0.0], []
    pace = consolidation.Pace(clock=lambda: now[0], sleep=slept.append)
    pace()
    assert slept == [], "no rest before a slice is worked"
    now[0] += consolidation.WORK_SLICE_SECS
    pace()
    assert slept == [consolidation.REST_SECS]
    pace()
    assert slept == [consolidation.REST_SECS], "the next slice starts after the rest"

    async def on_a_loop() -> None:
        now[0] += 1.0
        pace()

    asyncio.run(on_a_loop())
    assert slept == [consolidation.REST_SECS], "the thread of a loop is never put to sleep"
    assert pace.rests == 1


def test_the_planner_rests_before_each_comparison_and_each_step():
    calls = [0]

    def rest() -> None:
        calls[0] += 1

    items = [Item(id=f"k{i}", content=f"subject{i} word{i} other{i}") for i in range(20)]
    plan_consolidation(items, min_size=2, rest=rest)
    pairs = len(items) * (len(items) - 1) // 2
    # Each pair once in the dedup and once in the floor, and each item's one step twice.
    assert calls[0] == 2 * pairs + 2 * len(items), calls
    calls[0] = 0
    consolidation.tokens_of("word " * consolidation.TOKEN_STEP_CHARS, rest=rest)
    assert calls[0] == 5, "a rest before each of the text's five steps"


@pytest.fixture
def one_pass(monkeypatch):
    """The registry holding one pass of the test's own, restored after."""
    saved = dict(maintenance._PASSES)
    maintenance.clear_passes()
    monkeypatch.setattr(maintenance, "load_state", lambda: {"dirty_ts": 1.0})
    monkeypatch.setattr(maintenance, "clear_up_to", lambda _snapshot: None)
    yield
    maintenance._PASSES.clear()
    maintenance._PASSES.update(saved)


def test_each_pass_is_timed_and_the_running_one_is_named(one_pass):
    seen: list[maintenance.Running | None] = []

    def slow(*, batch_size: int = 0) -> int:
        seen.append(maintenance.running())
        time.sleep(0.05)
        return 0

    maintenance.register_pass("knowledge_consolidation", slow, batched=False)
    result = maintenance.execute()
    (now,) = seen
    assert now is not None and now.step == "knowledge_consolidation", now
    assert maintenance.running() is None, "nothing runs once the run is over"
    assert result.per_pass_secs["knowledge_consolidation"] >= 0.05, result.per_pass_secs


def test_the_running_list_names_the_pass_while_it_runs(one_pass):
    """What the owner sees while the gateway is slow to answer: the library's upkeep, by pass,
    where every other piece of live work is listed (`GET /api/running`)."""
    from personalclaw import running_now

    rows: list[list[dict]] = []

    def watched(*, batch_size: int = 0) -> int:
        live, _ended = running_now.read(
            running_now.Snapshot(rows=[], waits={}, helpers=[], said={}, tail=[], chats={})
        )
        rows.append([r for r in live if r["kind"] == "maintenance"])
        return 0

    maintenance.register_pass("knowledge_consolidation", watched, batched=False)
    maintenance.execute()
    ((row,),) = rows
    assert (row["work"], row["kind"], row["name"]) == (
        "maintenance:knowledge",
        "maintenance",
        "Tidying the knowledge library",
    ), row
    assert row["line"] == "Grouping items that say the same thing" and row["route"] == "#/knowledge"
    assert row["started_at"], row
    assert running_now._maintenance() == [], "nothing is listed once the run is over"


def test_a_slow_run_names_its_passes_in_the_log(monkeypatch, caplog):
    """What names the pass when the gateway was slow, without a native stack sample."""
    import logging

    from personalclaw.durability import service

    slow = maintenance.MaintenanceResult(
        ran=True,
        per_pass={"knowledge_consolidation": 0, "similarity_edges": 3},
        per_pass_secs={"knowledge_consolidation": 41.2, "similarity_edges": 0.4},
    )
    monkeypatch.setattr(maintenance, "run_maintenance", lambda: slow)
    with caplog.at_level(logging.INFO, logger=service.logger.name):
        service._tick_graph_maintenance()
    said = [r.getMessage() for r in caplog.records if "took" in r.getMessage()]
    assert said == [
        "graph maintenance took 41.6 s: knowledge_consolidation 41.2 s, similarity_edges 0.4 s"
    ], said
