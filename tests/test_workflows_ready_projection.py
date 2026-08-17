"""The ready projection on the unified admission core (PP-13).

`pool.frontier`/`pool.next_task` were a second scheduler: a private projection answering *what may
be worked now* with its own ordering and its own leased-work filter, beside the admission core that
answers the same question for the engine. `PP-13` retires it. The ordering became
`admission.rank_key`, the exclusion became the composed `Lease` policy, and `admission.ready` is the
two of them together.

**The deliverable is the equivalence proof, and it is a golden file for the same reason `PP-11`'s
was.** There is no oracle for "does the new scheduler still decide the same thing" once the old one
is deleted, so the old one's output was captured BEFORE the delete, from an unmodified `pool.py`
(`ef8497ed5d29005d6df4cd4acfdba261a2fdedf2facca497f3410dee7627f36f`) at commit `854529a2`, over a
seeded fixture rich enough for the claim to mean something. Both sides read ONE seed file: a
re-stated seed is a second implementation of the fixture, and it would drift.

The fixture is not regenerable — nothing survives to regenerate it from. That is the point of a
retirement, and it is why the vacuity floor below is a test rather than a comment: "identical" over
an empty or single-element list is trivially true, and a comparator proven on one item is proven on
nothing.
"""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

from personalclaw.workflows import admission, pool
from personalclaw.workflows.admission import (
    OBSERVER,
    PRIORITY_WEIGHT,
    AdmissionState,
    Limits,
    ReadyItem,
    Urgency,
    default_policies,
    explain,
    next_ready,
    ready,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "pool_frontier_golden"


# ── the seed, and the two sides that must agree over it ──


def _seed() -> dict:
    return json.loads((FIXTURES / "seed.json").read_text(encoding="utf-8"))


def _golden() -> list[dict]:
    lines = (FIXTURES / "ready.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _items(seed: dict) -> list[ReadyItem]:
    """The seed's candidates as core items. Field-for-field with the retired `Candidate`, except
    `leased_by` — occupancy is no longer a field ON the work, it is a lease record the policy reads,
    which is the substantive half of this retirement."""
    return [
        ReadyItem(
            item_id=c["task_id"],
            title=c["title"],
            priority=c["priority"],
            unblocked=c["unblocked"],
            blocks_count=c["blocks_count"],
            overdue=c["overdue"],
            updated_at=c["updated_at"],
        )
        for c in seed["candidates"]
    ]


def _leases(seed: dict, *, live: bool = True) -> dict[str, pool.Lease]:
    """The seed's `leased_by` fields as the lease records they stand for.

    `live=False` backdates them past their TTL, which is how the one deliberately-not-preserved
    behaviour is measured rather than asserted from the docstring.
    """
    now = seed["now"]
    ttl = seed["lease_ttl_secs"]
    acquired = (now - 60) if live else (now - ttl - 60)
    return {
        c["task_id"]: pool.Lease(
            task_id=c["task_id"], holder=c["leased_by"], acquired_at=acquired, ttl_seconds=ttl
        )
        for c in seed["candidates"]
        if c["leased_by"]
    }


def _observer_policies(seed: dict, *, live: bool = True) -> tuple:
    """The board's policy list: the same `default_policies` the engine's frontier composes, asked
    as the read-only `OBSERVER` identity."""
    return default_policies(
        Limits(),
        single_active_feature=False,
        state=AdmissionState(
            now=seed["now"], holder=seed["board_holder"], leases=_leases(seed, live=live)
        ),
    )


# ── the equivalence proof ──


def test_the_seeded_fixture_is_not_VACUOUS():
    """ "Identical before and after" over an empty list is a sentence with no content.

    Every clause here is a way the proof below could be trivially true, so each is denied
    explicitly: enough elements to have an order, a blocked item that must be dropped, an overdue
    item, a leased item, real score ties resolved by BOTH tie-breakers, and an order that is neither
    the seed's nor sorted by id.
    """
    seed = _seed()
    golden = _golden()
    exclusive = [r for r in golden if r["scenario"] == "exclusive"]
    inclusive = [r for r in golden if r["scenario"] == "inclusive"]

    assert len(exclusive) >= 5, f"only {len(exclusive)} ready rows — too few to have an order"
    assert len(inclusive) > len(
        exclusive
    ), "the leased scenarios must differ, or the lease is inert"

    seeded = {c["task_id"]: c for c in seed["candidates"]}
    ranked_ids = [r["task_id"] for r in exclusive]
    blocked = {tid for tid, c in seeded.items() if not c["unblocked"]}
    leased = {tid for tid, c in seeded.items() if c["leased_by"]}

    assert len(blocked) >= 1 and not (blocked & set(ranked_ids)), "no blocked item was excluded"
    assert len(leased) >= 2 and not (leased & set(ranked_ids)), "no leased item was excluded"
    assert any(r["urgency"] == Urgency.OVERDUE.value for r in exclusive), "no overdue item ranked"
    assert any(r["urgency"] == Urgency.BLOCKING_OTHERS.value for r in exclusive)
    assert len({r["score"] for r in exclusive}) >= 4, "too few distinct scores to order by score"

    scores = [r["score"] for r in exclusive]
    assert len(scores) != len(set(scores)), "no score TIE, so neither tie-breaker is exercised"
    assert ranked_ids != [
        c["task_id"] for c in seed["candidates"] if c["task_id"] in ranked_ids
    ], "the ranked order equals the seed order — the sort could be a no-op"
    assert ranked_ids != sorted(
        ranked_ids
    ), "the ranked order is id order — the score could be flat"


def test_the_ready_set_is_IDENTICAL_to_the_retired_projection_order_included():
    """The atom's bar, element for element: same items, same positions, same urgency, same score,
    same explanation line. Order is part of the contract — priority, then blocking-count, then
    overdue, then recency, then id — so a set comparison would pass while the top task changed.
    """
    seed = _seed()
    items = _items(seed)

    computed = {
        "exclusive": ready(items, _observer_policies(seed)),
        # No `AdmissionState` means no `Lease` policy at all, so nothing speaks to a RESOURCE
        # bucket and every leased item is admitted — exactly what `include_leased=True` meant.
        "inclusive": ready(items, default_policies(Limits(), single_active_feature=False)),
    }

    for scenario, ranked in computed.items():
        expected = [r for r in _golden() if r["scenario"] == scenario]
        assert len(ranked) == len(expected), f"{scenario}: {len(ranked)} rows vs {len(expected)}"
        for row, item in zip(expected, ranked):
            where = f"{scenario}[{row['position']}]"
            assert item.item_id == row["task_id"], f"{where}: {item.item_id} != {row['task_id']}"
            assert item.urgency().value == row["urgency"], f"{where}: urgency"
            assert item.score() == pytest.approx(row["score"]), f"{where}: score"
            assert explain(item) == row["explain"], f"{where}: explanation"


def test_next_ready_is_IDENTICAL_to_the_retired_next_task():
    seed = _seed()
    expected = next(r for r in _golden() if r["scenario"] == "next")
    top = next_ready(_items(seed), _observer_policies(seed))
    assert top is not None and top.item_id == expected["task_id"]


def test_next_ready_is_the_ready_HEAD_by_construction():
    """One function, one answer — what stops "what should I work on" being reimplemented per
    surface, so the list and the pick cannot disagree."""
    seed = _seed()
    policies = _observer_policies(seed)
    items = _items(seed)
    assert next_ready(items, policies) is ready(items, policies)[0]


# ── the one behaviour the retirement deliberately does NOT preserve ──


def test_an_EXPIRED_lease_no_longer_hides_work():
    """`pool.frontier` filtered on `leased_by` being truthy, so it hid work whose holder was already
    gone — the board said "taken" about work that was free, and only a sweep could correct it. The
    `Lease` policy asks `pool.acquire`, which treats an expired lease as takeable, so the item
    surfaces immediately. Same reasoning `containers.board_row` already applies when it drops an
    expired claim badge rather than rendering it.
    """
    seed = _seed()
    held = [r["task_id"] for r in _golden() if r["scenario"] == "inclusive"]
    live = [item.item_id for item in ready(_items(seed), _observer_policies(seed))]
    dead = [item.item_id for item in ready(_items(seed), _observer_policies(seed, live=False))]

    leased = {c["task_id"] for c in seed["candidates"] if c["leased_by"]}
    assert not (leased & set(live)), "a LIVE lease must still exclude"
    assert leased <= set(dead), f"an expired lease still hid {sorted(leased - set(dead))}"
    assert dead == held, "with every lease expired the projection is the include-leased view"


# ── the leases themselves: still one implementation, still surviving a kill ──


def test_the_projection_reuses_pool_acquire_rather_than_re_deciding():
    """The exclusion is the pool's compare-and-swap decision, not a second lease rule. Asserted by
    breaking `pool.acquire`: if the projection had its own copy, this would not move."""
    seed = _seed()
    calls: list[str] = []

    def _refuse_everything(existing, *, task_id, holder, now, ttl_seconds=0):
        calls.append(task_id)
        return None, "held_by_other"

    original = pool.acquire
    admission.pool.acquire = _refuse_everything  # type: ignore[assignment]
    try:
        out = ready(_items(seed), _observer_policies(seed))
    finally:
        admission.pool.acquire = original  # type: ignore[assignment]

    assert out == [], "the projection kept its own lease rule"
    assert len(calls) >= 8, f"only {len(calls)} items were put to the lease decision"


def test_a_lease_SURVIVES_a_gateway_kill(tmp_path, monkeypatch):
    """A lease lives in a sidecar file, so a gateway that dies mid-claim must not release it.

    Driven through the real write path (`pool.claim_task`, the flocked read-modify-write) and then
    re-read from disk with NO in-memory carry-over — the projection is handed a state built only
    from what `read_lease` finds, which is all a restarted gateway has.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    seed = _seed()
    now = seed["now"]
    items = [ReadyItem(item_id="t-a", priority="high"), ReadyItem(item_id="t-b", priority="high")]

    def _project(at: float) -> list[str]:
        """Everything a restarted process knows: the clock, and the lease files on disk."""
        rehydrated = {i.item_id: lease for i in items if (lease := pool.read_lease(i.item_id))}
        policies = default_policies(
            Limits(),
            single_active_feature=False,
            state=AdmissionState(now=at, holder=OBSERVER, leases=rehydrated),
        )
        return [item.item_id for item in ready(items, policies)]

    assert _project(now) == ["t-a", "t-b"], "nothing is leased yet"

    lease, error = pool.claim_task("t-a", holder="session-doomed", now=now, ttl_seconds=900)
    assert lease is not None and not error, f"claim refused: {error}"
    assert pool.leases_dir().joinpath("t-a.json").is_file(), "the lease was never persisted"

    # The kill: no state at all beyond the filesystem.
    assert _project(now + 1) == ["t-b"], "a persisted lease did not survive the restart"
    assert _project(now + 901) == ["t-a", "t-b"], "the lease outlived its own TTL"


# ── the retired names are GONE, and the surviving ones are where they moved ──


@pytest.mark.parametrize("name", ["frontier", "next_task", "Candidate", "Urgency", "explain"])
def test_the_retired_projection_is_DELETED_from_pool(name):
    """Retiring a legacy path is never a pure deletion, so this rail is two-directional: the name
    must be gone from `pool` AND present on the core. A deletion that left the name importable would
    keep the dual path alive, and one that dropped the capability would be a regression dressed as a
    cleanup."""
    assert not hasattr(pool, name), f"pool.{name} survived the retirement"


@pytest.mark.parametrize(
    "name",
    ["ready", "next_ready", "rank_key", "ReadyItem", "Urgency", "explain", "PRIORITY_WEIGHT"],
)
def test_the_projection_now_lives_on_the_admission_core(name):
    assert hasattr(admission, name), f"admission.{name} is missing after the move"


@pytest.mark.parametrize(
    "name", ["acquire", "renew", "release", "sweep_expired", "read_lease", "claim_task", "Lease"]
)
def test_the_lease_DECISION_functions_survive(name):
    """They are the `Lease` policy's implementation. Only the duplicate projection went."""
    assert hasattr(pool, name), f"pool.{name} was deleted with the projection"


# ── the relocated behaviour, verbatim in intent (was tests/test_workflows_pool.py) ──


def _item(**kw) -> ReadyItem:
    base = dict(item_id="t", title="", priority="medium", unblocked=True)
    base.update(kw)
    return ReadyItem(**base)  # type: ignore[arg-type]


def _free() -> tuple:
    """Policies with a lease state that holds nothing — the projection asking about an empty pool of
    claims, so only the ordering is under test."""
    return default_policies(
        Limits(), single_active_feature=False, state=AdmissionState(now=0.0, holder=OBSERVER)
    )


def test_the_ready_projection_EXCLUDES_blocked_items():
    items = [_item(item_id="a"), _item(item_id="b", unblocked=False)]
    assert [i.item_id for i in ready(items, _free())] == ["a"]


def test_the_ready_projection_EXCLUDES_leased_items():
    """A projection listing work another session actively holds invites exactly the
    double-execution the leases prevent."""
    items = [_item(item_id="a"), _item(item_id="b")]
    state = AdmissionState(
        now=100.0,
        holder=OBSERVER,
        leases={"b": pool.Lease(task_id="b", holder="session-x", acquired_at=100.0)},
    )
    policies = default_policies(Limits(), single_active_feature=False, state=state)
    assert [i.item_id for i in ready(items, policies)] == ["a"]


def test_the_board_can_ASK_for_leased_items():
    """The board also shows claims rather than picking work, so it needs the other view — and it
    gets it by composing WITHOUT the lease policy rather than by a boolean flag."""
    items = [_item(item_id="b")]
    state = AdmissionState(
        now=100.0,
        holder=OBSERVER,
        leases={"b": pool.Lease(task_id="b", holder="session-x", acquired_at=100.0)},
    )
    assert ready(items, default_policies(Limits(), single_active_feature=False, state=state)) == []
    assert len(ready(items, default_policies(Limits(), single_active_feature=False))) == 1


def test_higher_priority_ranks_first():
    items = [_item(item_id="low", priority="low"), _item(item_id="critical", priority="critical")]
    assert ready(items, _free())[0].item_id == "critical"


def test_an_item_BLOCKING_others_outranks_an_equal_that_blocks_nothing():
    """The whole point of a dependency-aware pool: a medium task blocking four others is worth more
    than a medium task blocking none."""
    items = [_item(item_id="alone"), _item(item_id="blocker", blocks_count=4)]
    assert ready(items, _free())[0].item_id == "blocker"


def test_OVERDUE_beats_priority():
    items = [_item(item_id="high", priority="high"), _item(item_id="late", overdue=True)]
    assert ready(items, _free())[0].item_id == "late"


def test_the_ORDER_IS_STABLE_for_equals():
    """An unstable "next task" makes an agent thrash between two equals."""
    items = [_item(item_id="b"), _item(item_id="a")]
    assert [i.item_id for i in ready(items, _free())] == ["a", "b"]
    assert [i.item_id for i in ready(list(reversed(items)), _free())] == ["a", "b"]


def test_next_ready_on_an_EMPTY_pool_is_None():
    assert next_ready([], _free()) is None


def test_next_ready_on_an_all_blocked_pool_is_None():
    assert next_ready([_item(unblocked=False)], _free()) is None


def test_the_priority_vocabulary_matches_the_TASK_model():
    """Two priority scales would disagree about a task, and the looser one would win."""
    from personalclaw.tasks.models import TaskPriority

    assert set(PRIORITY_WEIGHT) == {p.value for p in TaskPriority}


def test_urgency_is_REPORTED_not_just_used_for_sorting():
    """A ranked list whose order cannot be explained is one a user overrides — and then the
    projection is decoration."""
    assert _item(overdue=True).urgency() is Urgency.OVERDUE
    assert _item(blocks_count=2).urgency() is Urgency.BLOCKING_OTHERS
    assert _item(priority="high").urgency() is Urgency.HIGH_PRIORITY
    assert _item().urgency() is Urgency.NORMAL


def test_the_explanation_names_the_reasons():
    line = explain(_item(item_id="t-9", priority="high", blocks_count=3, overdue=True))
    assert "t-9" in line and "overdue" in line and "blocks 3" in line


# ── the unblock machinery the retirement had to leave alone ──


def test_the_dependency_failed_CASCADE_still_fires_after_the_retirement():
    """`plan_unblock` was never part of the projection, but "retiring a legacy path is never a pure
    deletion" is a claim about the whole module, so the cascade is re-measured here rather than
    assumed from the file that no longer imports the projection."""
    out = pool.plan_unblock(
        blocker_id="a", blocker_status="failed", blocker_reason="disk full", dependents={"b": ["a"]}
    )
    assert len(out) == 1
    assert out[0].kind is pool.UnblockKind.CASCADE_FAILED
    assert out[0].blocked_kind == "dependency_failed"
    assert "disk full" in out[0].reason


def test_BURST_coalescing_still_fires_after_the_retirement():
    """A parallel fan-in failure must be one notification, not N."""
    out = pool.plan_unblock(
        blocker_id="a",
        blocker_status="failed",
        blocker_reason="upstream died",
        dependents={"b": ["a"], "c": ["a"], "d": ["a"]},
    )
    transitions, summary = pool.coalesce(out)
    assert len(transitions) == 3
    assert summary.startswith("3 tasks blocked by one upstream failure"), summary
    assert "upstream died" in summary


# ── the wired surface: the Work board's ready projection actually reads the core ──


class TestReadyTasksIsRanked:
    """`registry.ready_tasks` is the one funnel `/api/tasks/ready`, the dashboard slice and the
    agent's next-task tool all share. Before `PP-13` it returned PROVIDER order — so the complete
    ranking in `pool.frontier` was reachable by nobody, and the ranked projection the plan promised
    was, in the surface a user actually sees, unsorted."""

    @pytest.fixture(autouse=True)
    def _own_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        yield

    def _run(self, monkeypatch, tasks):
        from personalclaw.tasks import registry

        async def _list(**kwargs):
            return list(tasks), len(tasks)

        monkeypatch.setattr(registry, "list_all_tasks", _list)
        monkeypatch.setattr("personalclaw.identity.current_username", lambda: "")
        return [t.id for t in asyncio.run(registry.ready_tasks())]

    def _task(self, tid: str, **kw):
        from personalclaw.tasks.models import Task, TaskPriority

        return Task(
            id=tid,
            title=tid,
            priority=TaskPriority.normalize(kw.pop("priority", "medium")),
            **kw,
        )

    def test_the_funnel_returns_the_cores_ORDER_not_provider_order(self, monkeypatch):
        tasks = [
            self._task("t-trivial", priority="trivial"),
            self._task("t-critical", priority="critical"),
            self._task("t-medium"),
            self._task("t-high", priority="high"),
        ]
        assert self._run(monkeypatch, tasks) == ["t-critical", "t-high", "t-medium", "t-trivial"]

    def test_a_blocking_task_is_ranked_by_its_DEPENDENTS_not_its_peers(self, monkeypatch):
        """`blocks_count` is counted over the FULL task map. Counting only among ready peers would
        score every bottleneck at zero, because a bottleneck's dependents are the BLOCKED tasks."""
        from personalclaw.tasks.models import TaskDependency

        deps = [TaskDependency(depends_on_task_id="t-blocker")]
        tasks = [
            self._task("t-high", priority="high"),
            self._task("t-blocker", priority="low"),
            self._task("d1", dependencies=list(deps)),
            self._task("d2", dependencies=list(deps)),
            self._task("d3", dependencies=list(deps)),
            self._task("d4", dependencies=list(deps)),
        ]
        out = self._run(monkeypatch, tasks)
        assert (
            out[0] == "t-blocker"
        ), f"a low task blocking four others ranked {out.index('t-blocker')}"

    def test_an_OVERDUE_task_is_ranked_from_its_due_date(self, monkeypatch):
        """`Task.due` had no reader anywhere in the product before this — the overdue term of the
        ranking was scoring a field nothing computed, on every surface.

        Overdue is a +2.0 BUMP, not an override, which is measured here rather than assumed: an
        overdue medium (4.0) outranks a plain high (3.0) and stays under a critical (5.0). Asserting
        "overdue beats priority" flatly would have passed against a comparator that clamped the
        bump, and it would have been wrong about the shipped rule.
        """
        # Deliberately NOT in the expected order. Measured: with the input pre-sorted, this test
        # stayed green while the funnel was mutated to skip the core entirely — provider order and
        # the ranked order were the same list, so it proved nothing about the wiring.
        tasks = [
            self._task("t-future", priority="medium", due="2999-01-01T00:00:00+00:00"),
            self._task("t-high", priority="high"),
            self._task("t-critical", priority="critical"),
            self._task("t-late", priority="medium", due="2001-01-01T00:00:00+00:00"),
        ]
        out = self._run(monkeypatch, tasks)
        assert out == ["t-critical", "t-late", "t-high", "t-future"], out

    def test_a_MALFORMED_due_date_costs_a_tie_break_not_the_projection(self, monkeypatch):
        tasks = [self._task("t-a", due="not a date"), self._task("t-b", updated_at="also not")]
        assert sorted(self._run(monkeypatch, tasks)) == ["t-a", "t-b"]

    def test_the_funnel_EXCLUDES_a_task_another_holder_is_leasing(self, monkeypatch):
        """The board's exclusion is now the same composed verdict the engine's frontier gets, so a
        real claim on disk removes the task from the one funnel every picker reads."""
        import time as _time

        tasks = [self._task("t-free", priority="high"), self._task("t-held", priority="critical")]
        assert self._run(monkeypatch, tasks) == ["t-held", "t-free"]
        lease, error = pool.claim_task(
            "t-held", holder="session-other", now=_time.time(), ttl_seconds=900
        )
        assert lease is not None and not error, f"claim refused: {error}"
        assert self._run(monkeypatch, tasks) == ["t-free"]
