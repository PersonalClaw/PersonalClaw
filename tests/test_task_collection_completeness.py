"""A task collection is COMPLETE, and a window says it is one (issue 485).

`/api/tasks` took the server's default `limit=50` because no caller sent one, and every
consumer of that window then derived facts from it as though it were the whole set. The
truncation is invisible in every direction:

  * `block_reason` resolves each prerequisite id against the set it is handed, and an ABSENT
    prerequisite counts as satisfied — so a task on page 2 whose blocker sits on page 1 is
    reported `is_blocked: false`. Not a missing edge: a wrong answer;
  * the dependency graph filters edges to the ids it received (`web/src/pages/tasks/dag.ts`),
    so an off-page prerequisite renders as a clean unblocked node;
  * the prerequisite picker and the "what depends on this" list can only see the window.

Underneath that, the aggregator asked each provider for `limit=500, offset=0` exactly once
and then reported `len(collected)` as `total` — so `total` itself capped at 500 per provider,
and a client could not page past it even by asking.

The fix is a named primitive for the thing fourteen call sites were spelling `limit=10_000`:
`collect_tasks` pages each provider to exhaustion and says whether its own safety bound cut
the result short. `list_all_tasks` keeps the windowed shape for the callers that genuinely
serve a window.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from fakes import FakeTaskProvider, only_task_provider

from personalclaw.tasks import handlers, registry
from personalclaw.tasks.models import Task, TaskDependency, TaskStatus


def _task(tid: str, **kw) -> Task:
    return Task(id=tid, title=kw.pop("title", tid), **kw)


def _many(n: int, prefix: str = "t") -> list[Task]:
    # Descending `updated_at` so the aggregator's newest-first sort is checkable and stable.
    return [
        _task(f"{prefix}-{i:05d}", updated_at=f"2026-01-01T00:00:{n - i:02d}Z") for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    """Never the real home: `_ensure_native` would otherwise read the owner's tasks."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    yield


# ── the aggregator pages a provider to completeness ──────────────────────────────────────────


def test_a_provider_holding_more_than_one_page_is_paged_until_exhausted(monkeypatch):
    """🔑 The core regression. One request per provider is what capped the whole seam at 500."""
    prov = FakeTaskProvider(_many(1201))
    only_task_provider(monkeypatch, prov)

    tasks, truncated = asyncio.run(registry.collect_tasks())

    assert len(tasks) == 1201, "the aggregate is the provider's whole set, not its first page"
    assert truncated is False
    assert prov.requests == [
        (500, 0),
        (500, 500),
        (500, 1000),
    ], "each page must be asked for at the offset the previous one ended at"


def test_a_small_install_costs_one_request(monkeypatch):
    """Vacuity guard for the paging loop: the ordinary small install must not pay for it. The
    provider's own count settles it — 26 of 26 rows in hand is exhaustion, no probe needed."""
    prov = FakeTaskProvider(_many(26))
    only_task_provider(monkeypatch, prov)

    tasks, truncated = asyncio.run(registry.collect_tasks())

    assert len(tasks) == 26
    assert truncated is False
    assert prov.requests == [(500, 0)], "26 of 26 rows is exhaustion by the provider's own count"


def test_a_set_that_ends_exactly_on_a_page_boundary_is_complete(monkeypatch):
    """🪤 The off-by-one that a `len(page) < PAGE` test alone would miss: 500 rows means a FULL
    first page, so the loop must consult the provider's own total to know it is done."""
    prov = FakeTaskProvider(_many(500))
    only_task_provider(monkeypatch, prov)

    tasks, truncated = asyncio.run(registry.collect_tasks())

    assert len(tasks) == 500
    assert truncated is False


def test_rows_from_every_provider_are_merged_and_sorted_newest_first(monkeypatch):
    a = FakeTaskProvider([_task("old", updated_at="2026-01-01T00:00:00Z")], name="a")
    b = FakeTaskProvider([_task("new", updated_at="2026-06-01T00:00:00Z")], name="b")
    monkeypatch.setattr(registry, "_providers", {"a": a, "b": b})
    monkeypatch.setattr(registry, "_ensure_native", lambda: None)

    tasks, _ = asyncio.run(registry.collect_tasks())

    assert [t.id for t in tasks] == ["new", "old"]


def test_a_provider_that_fails_does_not_lose_the_others(monkeypatch):
    class Broken(FakeTaskProvider):
        async def list_tasks(self, **kw):  # type: ignore[override]
            raise RuntimeError("provider down")

    monkeypatch.setattr(
        registry,
        "_providers",
        {"ok": FakeTaskProvider([_task("kept")], name="ok"), "bad": Broken(name="bad")},
    )
    monkeypatch.setattr(registry, "_ensure_native", lambda: None)

    tasks, _ = asyncio.run(registry.collect_tasks())

    assert [t.id for t in tasks] == ["kept"]


# ── the bound is real, reported, and the loop's termination guarantee ─────────────────────────


def test_the_per_provider_bound_truncates_and_SAYS_SO(monkeypatch):
    """A bound is fine; a bound that reads as completion is the defect itself."""
    monkeypatch.setattr(registry, "MAX_ROWS_PER_PROVIDER", 1000)
    prov = FakeTaskProvider(_many(1500))
    only_task_provider(monkeypatch, prov)

    tasks, truncated = asyncio.run(registry.collect_tasks())

    assert len(tasks) == 1000
    assert truncated is True, "the caller cannot disclose a truncation it was not told about"


def test_a_provider_that_ignores_offset_still_terminates(monkeypatch):
    """🔑 Why the bound is not merely a memory guard. This provider re-serves page 0 forever, so
    without it the request never returns — a hang, not a wrong number."""
    monkeypatch.setattr(registry, "MAX_ROWS_PER_PROVIDER", 1000)
    prov = FakeTaskProvider(_many(600), ignore_offset=True, total_override=10_000)
    only_task_provider(monkeypatch, prov)

    tasks, truncated = asyncio.run(asyncio.wait_for(registry.collect_tasks(), timeout=10))

    assert truncated is True
    assert len(tasks) >= 1000


def test_a_provider_whose_total_overstates_what_it_serves_stops_and_reports_the_gap(monkeypatch):
    """The other direction: `total` says 900, the provider runs dry at 600. An EMPTY page ends the
    walk — and the gap is disclosed, because by the provider's own count 300 rows exist that it
    will not hand over."""
    prov = FakeTaskProvider(_many(600), total_override=900)
    only_task_provider(monkeypatch, prov)

    tasks, truncated = asyncio.run(asyncio.wait_for(registry.collect_tasks(), timeout=10))

    assert len(tasks) == 600
    assert truncated is True


def test_a_provider_that_clamps_its_page_size_is_still_paged_to_completeness(monkeypatch):
    """🔑 Why a SHORT page cannot mean "out of rows". A remote provider is free to clamp `limit` to
    its own maximum, so asking for 500 and getting 100 says nothing about what remains. Stopping
    there would reintroduce the exact defect — a silent 100-row window — one layer down."""
    prov = FakeTaskProvider(_many(450), max_page=100)
    only_task_provider(monkeypatch, prov)

    tasks, truncated = asyncio.run(asyncio.wait_for(registry.collect_tasks(), timeout=10))

    assert len(tasks) == 450, "a clamped page must not be read as exhaustion"
    assert truncated is False
    assert [off for _, off in prov.requests] == [0, 100, 200, 300, 400]


# ── the filters still apply, and now compose with the window ──────────────────────────────────


def test_filters_are_passed_on_every_page(monkeypatch):
    prov = FakeTaskProvider(
        _many(600) + [_task("done-1", status=TaskStatus.DONE, updated_at="2026-09-01T00:00:00Z")]
    )
    only_task_provider(monkeypatch, prov)

    tasks, _ = asyncio.run(registry.collect_tasks(status="done"))

    assert [t.id for t in tasks] == ["done-1"]


def test_the_owner_lens_filters_the_whole_set_not_the_page(monkeypatch):
    """🔑 `mine=1` used to be applied AFTER the slice, so it shrank the page (a 50-row window
    could yield 3 rows) and then reported `total` as the size of what survived. Filtering with
    the other post-aggregation filters is what makes it compose with limit/offset."""
    rows = [
        _task(f"mine-{i}", assignee="keyur", updated_at=f"2026-01-02T00:00:{i:02d}Z")
        for i in range(5)
    ]
    rows += [
        _task(f"dana-{i}", assignee="dana", updated_at=f"2026-01-01T00:00:{i:02d}Z")
        for i in range(5)
    ]
    only_task_provider(monkeypatch, FakeTaskProvider(rows))

    tasks, _ = asyncio.run(registry.collect_tasks(owner="keyur"))

    assert len(tasks) == 5
    assert all(t.assignee == "keyur" for t in tasks)


def test_no_owner_means_no_ownership_filter(monkeypatch):
    only_task_provider(monkeypatch, FakeTaskProvider([_task("a", assignee="dana")]))
    tasks, _ = asyncio.run(registry.collect_tasks())
    assert [t.id for t in tasks] == ["a"]


# ── list_all_tasks is now a WINDOW of that collection ─────────────────────────────────────────


def test_the_window_helper_reports_the_whole_set_as_total(monkeypatch):
    """It used to report `min(count, 500)` per provider, so a client could not even discover
    how much it was missing."""
    only_task_provider(monkeypatch, FakeTaskProvider(_many(720)))

    page, total = asyncio.run(registry.list_all_tasks(limit=50, offset=0))

    assert len(page) == 50
    assert total == 720


def test_successive_windows_do_not_overlap_or_skip(monkeypatch):
    only_task_provider(monkeypatch, FakeTaskProvider(_many(120)))

    first, total = asyncio.run(registry.list_all_tasks(limit=50, offset=0))
    second, _ = asyncio.run(registry.list_all_tasks(limit=50, offset=50))
    third, _ = asyncio.run(registry.list_all_tasks(limit=50, offset=100))

    seen = [t.id for t in first + second + third]
    assert total == 120
    assert len(seen) == 120
    assert len(set(seen)) == 120, "a window boundary must not repeat or drop a row"


# ── the endpoint: a page, an honest total, and a completeness flag ────────────────────────────


async def _client(monkeypatch, rows: list[Task], **kw) -> TestClient:
    only_task_provider(monkeypatch, FakeTaskProvider(rows, **kw))
    app = web.Application()
    app.router.add_get("/api/tasks", handlers.api_tasks_list)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_block_reason_is_derived_from_the_whole_set_not_the_page(monkeypatch):
    """🔑 THE WORST CONSEQUENCE, and the one no client-side fix could reach. `block_reason`
    treats a prerequisite it cannot find as satisfied, so a blocker one page away made the
    server itself answer "not blocked" for a task that is."""
    blocker = _task("blocker", status=TaskStatus.OPEN, updated_at="2026-01-01T00:00:00Z")
    filler = _many(60, prefix="filler")  # pushes the blocker off the default 50-row window
    blocked = _task(
        "blocked",
        updated_at="2026-12-01T00:00:00Z",  # newest → first row of page 1
        dependencies=[TaskDependency(depends_on_task_id="blocker")],
    )
    client = await _client(monkeypatch, [blocked, *filler, blocker])
    try:
        body = await (await client.get("/api/tasks")).json()
        row = next(t for t in body["tasks"] if t["id"] == "blocked")
        assert (
            row["block_reason"]["is_blocked"] is True
        ), "the prerequisite is off-page, not finished"
        assert row["block_reason"]["blocking_task_ids"] == ["blocker"]
        assert body["total"] == 62
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_page_still_honours_limit_and_offset(monkeypatch):
    client = await _client(monkeypatch, _many(120))
    try:
        body = await (await client.get("/api/tasks?limit=10&offset=110")).json()
        assert len(body["tasks"]) == 10
        assert body["total"] == 120
        assert body["limit"] == 10 and body["offset"] == 110
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_response_declares_completeness(monkeypatch):
    """The flag a paging client needs: without it, a provider-side bound ends the client's loop
    looking exactly like exhaustion."""
    client = await _client(monkeypatch, _many(30))
    try:
        assert (await (await client.get("/api/tasks")).json())["complete"] is True
    finally:
        await client.close()

    monkeypatch.setattr(registry, "MAX_ROWS_PER_PROVIDER", 100)
    client = await _client(monkeypatch, _many(300))
    try:
        body = await (await client.get("/api/tasks")).json()
        assert body["complete"] is False
        assert body["total"] == 100, "total describes what was collected, and it is not everything"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mine_pages_correctly_rather_than_shrinking_the_page(monkeypatch):
    """The composed case the old post-slice filter could not answer: 60 of mine, 60 not, asked
    for a 50-row page. Before, the page arrived pre-sliced and was then filtered — so the client
    got ~25 rows and a `total` of 25."""
    # INTERLEAVED on purpose: with the owner's work merely newest, a 50-row window happens to be
    # all theirs and the shrunken-page symptom hides. Alternating timestamps means a page sliced
    # before filtering holds ~25 of each.
    mine = [
        _task(f"m-{i}", assignee="keyur", updated_at=f"2026-02-01T00:{i:02d}:00Z")
        for i in range(60)
    ]
    theirs = [
        _task(f"t-{i}", assignee="dana", updated_at=f"2026-02-01T00:{i:02d}:30Z") for i in range(60)
    ]
    monkeypatch.setattr("personalclaw.identity.current_username", lambda: "keyur")
    client = await _client(monkeypatch, mine + theirs)
    try:
        body = await (await client.get("/api/tasks?mine=1&limit=50")).json()
        assert len(body["tasks"]) == 50, "a full page of the owner's work, not a filtered remnant"
        assert body["total"] == 60, "and the total describes the owner's set"
        assert all(t["assignee"] == "keyur" for t in body["tasks"])
    finally:
        await client.close()


# ── the "all of them" callers no longer spell it with a number ────────────────────────────────


def test_no_caller_still_asks_for_everything_with_a_magic_limit():
    """The census that motivated the primitive, kept executable. Fourteen call sites meant "all
    of them" and said `limit=10_000` / `limit=500`; each was a silent truncation waiting for a
    busy install (a loop's progress count, a project delete, a reset gate). A window is fine
    where a window is intended — those callers name a small explicit one."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "src"
    offenders = [
        f"{path.relative_to(root)}:{i}"
        for path in root.rglob("*.py")
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"list_all_tasks\([^)]*limit=(?:500|10_000|10000)", line)
    ]
    assert offenders == [], f"these mean collect_tasks(): {offenders}"
