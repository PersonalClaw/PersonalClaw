"""One definition of "open", and a count that only moves when the user ACTS (issue 493).

Two definitions of "open" coexisted on one screen. `GET /api/inbox/status` published
`pending_count` (PENDING only) and the header rendered it; every filter, classification count and
kind chip on the same page used `isOpen = pending | seen`. Measured against a real gateway on an
isolated home seeded with 33 PENDING / 4 SEEN / 2 HANDLED / 2 DISMISSED / 1 FILTERED:

    header 33  ·  Open filter 37  ·  proposal chip 23  ·  GET /api/inbox/pending → 33 rows

Neither number was wrong on its own terms, so neither side looked broken. Then POSTing
`/api/inbox/seen` for ONE row — which is exactly what opening an item in the UI does — moved the
header to 32 and the pending feed to 32 while the filters and chips stayed at 37/23 and nothing was
resolved. Attention state changed because the user LOOKED. And the `dismiss-all` confirm was sized
from the same field, so it offered to "dismiss all 33 pending items" and the endpoint answered
`{"dismissed": 37}`.

The rule these rails pin: **a row you have read but not answered is still your work.** So SEEN is
open, `inbox.OPEN_STATUSES` is the one owner every count/filter/chip/sweep/dedup/digest reads, the
wire carries ONE count (`open_count`), and "new" stays a per-ROW signal (the unread dot) rather than
a total. The census tests are the rail against a third definition reappearing: a second spelling of
the pair anywhere under `src/personalclaw/` or in the frontend fails here rather than in six months
on someone's screen.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from inbox_open_status_is_one_owner_shared import REPO
from table_rows import row

from personalclaw import inbox as inbox_mod
from personalclaw.dashboard import handlers_inbox as H
from personalclaw.inbox import (
    OPEN_STATUSES,
    InboxItem,
    InboxState,
    InboxStore,
    ItemStatus,
    expire_attention_items,
    resolve_attention_items,
)

#: The complement of :data:`OPEN_STATUSES`, owned HERE rather than beside it in `inbox.py`.
#:
#: Two reasons, and the second is the load-bearing one. (1) Nothing in production needs a "resolved"
#: set — every consumer asks "is this still open?" — so a constant there would be an inert surface
#: with only a test to read it, which the inert-surface rail correctly flags. (2) Writing the closed
#: side out in the TEST is what makes the partition assertion below a real gate: a new `ItemStatus`
#: member is classified by a human editing one of these two lists, never by a subtraction that
#: quietly absorbs it.
RESOLVED_STATUSES = frozenset(
    {
        "sent",
        "dismissed",
        "handled",
        # Withheld by verification, not resolved BY THE USER — but off every attention surface by
        # decision: re-counting a row the verifier held back would undo that hold. It has
        # its own filter and its own Restore, which is how a false positive comes back.
        "filtered",
        # Ended before anyone answered it (an approval out of time, or whose work stopped first):
        # nothing is left to decide, and nobody decided anything either, so it is not "handled".
        "expired",
    }
)

WEB = REPO / "web" / "src"


# ── the vocabulary itself ──


def test_open_and_resolved_partition_the_status_enum():
    """Every status is on exactly one side, so a NEW member cannot default into either.

    Spelled out rather than computed as `set(ItemStatus) - OPEN_STATUSES` for the reason
    `SOURCE_DECLARABLE_KINDS` is: subtraction silently absorbs a new member, and which side a new
    attention state falls on is a product decision. This test is where that decision gets forced.
    """
    every = {s.value for s in ItemStatus}
    assert OPEN_STATUSES | RESOLVED_STATUSES == every, (
        "a status is classified as neither open nor resolved — decide which it is in inbox.py "
        f"(unclassified: {sorted(every - (OPEN_STATUSES | RESOLVED_STATUSES))})"
    )
    assert not (OPEN_STATUSES & RESOLVED_STATUSES), "a status cannot be both open and resolved"
    # The rule itself, stated so a future flip of it is a deliberate, visible edit.
    assert ItemStatus.SEEN.value in OPEN_STATUSES, (
        "SEEN is OPEN: having looked at a row is not having dealt with it. Moving it to "
        "the resolved side restores the defect issue 493 measured — a count that falls when the "
        "user merely glances at a row."
    )
    assert ItemStatus.PENDING.value in OPEN_STATUSES


def test_open_statuses_accepts_enum_members_not_only_strings():
    """Membership works for `ItemStatus.PENDING`, not just `"pending"`.

    `InboxStore.update(..., status=ItemStatus.DISMISSED)` writes the ENUM, so an in-memory item's
    `status` may be either. A frozenset of `.value` strings only answers correctly for the enum
    because `str` precedes `Enum` in the MRO and `str.__hash__` wins. That is load-bearing here, so
    it is asserted rather than assumed.
    """
    assert ItemStatus.PENDING in OPEN_STATUSES
    assert ItemStatus.SEEN in OPEN_STATUSES
    assert ItemStatus.HANDLED not in OPEN_STATUSES
    assert ItemStatus.FILTERED not in OPEN_STATUSES


def test_the_frontend_open_set_matches_the_server():
    """The frontend's exhaustive `STATUS_OPEN` and the server's two frozensets agree, exactly.

    The frontend cannot import Python, so parity is asserted here — the one place that can read
    both. Flipping `seen: false` in `attentionLanes.ts` (or adding a status on one side only) reds
    here instead of putting two numbers on one screen again.
    """
    src = (WEB / "lib" / "attentionLanes.ts").read_text(encoding="utf-8")
    block = re.search(
        r"export const STATUS_OPEN: Record<InboxItemStatus, boolean> = \{(.*?)\n\}", src, re.S
    )
    assert block, "STATUS_OPEN is no longer an exported exhaustive record in lib/attentionLanes.ts"
    entries = dict(re.findall(r"^\s*(\w+):\s*(true|false)", block.group(1), re.M))
    assert entries, "could not parse STATUS_OPEN"
    assert {k for k, v in entries.items() if v == "true"} == set(OPEN_STATUSES)
    assert {k for k, v in entries.items() if v == "false"} == set(RESOLVED_STATUSES)


#: Two redefinitions of "open", each the kind of edit to `inbox.py` a future change might make: one
#: drops SEEN (the edit issue 493 undid), one adds DISMISSED. They move rows in opposite directions,
#: so a consumer holding its own copy of the real pair answers differently from both.
_REDEFINED = (
    ("SEEN dropped", frozenset({"pending"})),
    ("DISMISSED added", frozenset({"pending", "seen", "dismissed"})),
)


def _holders(value) -> list[tuple[ModuleType, str]]:
    """Every module-level name in the product that holds *value*, the object itself."""
    found = []
    for mod in list(sys.modules.values()):
        name = getattr(mod, "__name__", None)
        if not isinstance(mod, ModuleType) or not isinstance(name, str):
            continue
        if name == "personalclaw" or name.startswith("personalclaw."):
            found += [(mod, attr) for attr, held in list(vars(mod).items()) if held is value]
    return found


@contextmanager
def _open_redefined(to: frozenset[str]) -> Iterator[pytest.MonkeyPatch]:
    """Make *to* the one open set: every name in the product that holds the owner holds *to*
    instead, as if `inbox.OPEN_STATUSES` had been written so. A consumer that reads the owner, by
    its import or at call time, answers by *to*; a private copy is another object, keeps the
    members it was written with, and answers as before.

    A module first imported inside takes *to* from `inbox`, so it gets the owner back on the way
    out with the rest: nothing after this test sees the redefinition."""
    with pytest.MonkeyPatch.context() as mp:
        for mod, attr in _holders(OPEN_STATUSES):
            mp.setattr(mod, attr, to)
        assert inbox_mod.OPEN_STATUSES is to
        try:
            yield mp
        finally:
            for mod, attr in _holders(to):
                setattr(mod, attr, OPEN_STATUSES)


def _open_ids(items, open_set) -> set[str]:
    return {it.id for it in items if it.status in open_set}


def _unsorted(i: int, status: str) -> InboxItem:
    """A message from a polled source that nobody has sorted, which `POST /api/inbox/{id}/sort`
    may send to the sorter while it is open."""
    it = _item(i, status=status, kind="message")
    it.classification = ""
    it.source = "filesystem"
    return it


def _sort(state, item_id: str) -> tuple[int, dict]:
    app = web.Application()
    app["state"] = state
    req = make_mocked_request(
        "POST", f"/api/inbox/{item_id}/sort", match_info={"id": item_id}, app=app
    )
    resp = asyncio.run(H.api_inbox_sort(req))
    return resp.status, json.loads(resp.body)


def _the_inbox_handlers(home, monkeypatch, to) -> None:
    """The Inbox page's counts and doors that ask whether a row is open: the kind chips, the owner
    chips, the header counts, the open feed, and which rows a sort may be asked for."""
    unsorted = [
        _unsorted(11, ItemStatus.PENDING.value),
        _unsorted(12, ItemStatus.SEEN.value),
        _unsorted(13, ItemStatus.DISMISSED.value),
    ]
    items = [*_seed(), *unsorted]
    want = _open_ids(items, to)
    assert want != _open_ids(items, OPEN_STATUSES), "the redefinition must move a row"
    st = _dashboard(home, monkeypatch, items)

    kinds = {k["kind"]: k["open"] for k in _get(st, "/api/inbox/kinds")["kinds"]}
    assert kinds == {
        kind: sum(1 for it in items if it.item_kind == kind and it.id in want)
        for kind in ("message", "proposal", "system")
    }, f"the kind chips count {kinds}"
    owners = _get(st, "/api/inbox/owners")["owners"]
    assert sum(o["open"] for o in owners) == len(want), f"the owner chips count {owners}"
    status = _get(st, "/api/inbox/status")
    assert status["open_count"] == len(want), "the header count"
    assert status["my_open_count"] == len(want), "the header's my-items count"
    assert {r["id"] for r in _get(st, "/api/inbox/open")} == want, "the open feed"
    for it in unsorted:
        code, body = _sort(st, it.id)
        if it.id in want:
            assert code == 200 and body["id"] == it.id, f"sorting open {it.status} answered {code}"
        else:
            assert code == 409, f"sorting closed {it.status} answered {code}"
            assert body["error"]["code"] == "inbox_item_not_sortable"


def _the_renotify_policy(home, monkeypatch, to) -> None:
    """A stale card is reminded about while open, and stays quiet once it is not, for every
    status there is."""
    from personalclaw.workflows.needs_input import (
        RENOTIFY_AFTER_HOURS,
        BlockKind,
        NeedsInputItem,
        should_renotify,
    )

    now = 1_000_000.0
    stale = NeedsInputItem(
        run_id="run-1",
        node_id="node-1",
        block_kind=BlockKind.NEEDS_INPUT,
        blocker="b",
        created_at=now - (RENOTIFY_AFTER_HOURS + 1) * 3600,
    )
    answers = {s.value: should_renotify(stale, now=now, status=s.value) for s in ItemStatus}
    assert {s for s, (ok, _) in answers.items() if ok} == set(to), answers
    for s, (ok, why) in answers.items():
        assert ok or why == f"card is {s}", (s, why)


#: The rows a gate's run left: one at node n1 for each status a close could meet, a sibling gate's
#: row at n2, and a row of another run. (id, status, node, run)
_GATE_ROWS = (
    ("n1-pending", ItemStatus.PENDING, "n1", "run-1"),
    ("n1-seen", ItemStatus.SEEN, "n1", "run-1"),
    ("n1-dismissed", ItemStatus.DISMISSED, "n1", "run-1"),
    ("n1-handled", ItemStatus.HANDLED, "n1", "run-1"),
    ("n2-pending", ItemStatus.PENDING, "n2", "run-1"),
    ("other-run", ItemStatus.PENDING, "n1", "run-2"),
)

_ENDED = "the workflow run was cancelled"


def _closer(door, target: ItemStatus, scope: tuple[str, ...]):
    """One door that closes a gate's rows: *door* closes, *target* is the status it closes to,
    *scope* the nodes of run-1 whose rows it may close."""

    def check(home, monkeypatch, to) -> None:
        home.mkdir()
        store = InboxStore(path=home / "inbox.json")
        for row_id, status, node, run in _GATE_ROWS:
            it = _item(len(store.items), status=status, kind="needs_input")
            it.id = row_id
            it.refs = {"workflow": run, "workflow_node": node}
            store.add(it)
        st = SimpleNamespace(_inbox_svc=SimpleNamespace(inbox=store, state=None))
        want = {
            row_id
            for row_id, status, node, run in _GATE_ROWS
            if run == "run-1" and node in scope and status in to
        }
        closed = door(st, store)
        assert closed == len(want), f"closed {closed}, wanted {sorted(want)}"
        for row_id, status, _node, _run in _GATE_ROWS:
            it = store.items[row_id]
            assert it.status == (target.value if row_id in want else status.value), row_id
            ended = it.refs.get("ended")
            if target is ItemStatus.EXPIRED and row_id in want:
                assert ended == _ENDED, f"{row_id} does not say why it expired"
            else:
                assert ended is None, f"{row_id} carries an end it was not given: {ended!r}"

    return check


def _gate_lane(name: str):
    from personalclaw.workflows import attention

    return getattr(attention, name)


_N1 = {"workflow": "run-1", "workflow_node": "n1"}

#: Each consumer, checked under a redefined owner. The shared closers are the one implementation
#: every emitter closes through, an answer (HANDLED) and an end with none (EXPIRED) alike; the
#: workflow gate lane's two doors close through them with its own refs.
_CONSUMERS = (
    ("the inbox handlers", _the_inbox_handlers),
    ("the renotify policy", _the_renotify_policy),
    (
        "inbox.resolve_attention_items",
        _closer(
            lambda st, store: resolve_attention_items(None, dict(_N1), store=store),
            ItemStatus.HANDLED,
            ("n1",),
        ),
    ),
    (
        "inbox.expire_attention_items",
        _closer(
            lambda st, store: expire_attention_items(None, dict(_N1), ended=_ENDED, store=store),
            ItemStatus.EXPIRED,
            ("n1",),
        ),
    ),
    (
        "attention.resolve_gate_item",
        _closer(
            lambda st, store: _gate_lane("resolve_gate_item")(st, "run-1", "n1"),
            ItemStatus.HANDLED,
            ("n1",),
        ),
    ),
    (
        "attention.expire_run_items",
        _closer(
            lambda st, store: _gate_lane("expire_run_items")(st, "run-1", ended=_ENDED),
            ItemStatus.EXPIRED,
            ("n1", "n2"),
        ),
    ),
)


def test_every_consumer_reads_the_owner(tmp_path, subtests):
    """The call sites read the owner rather than carrying a private copy.

    Named individually because each one was its own spelling before, and because the census in the
    rails half proves only that no SECOND definition exists — not that these modules consume the
    first. So "open" is redefined, as an edit to `inbox.py` would redefine it, and each consumer
    must answer by the new set: the inbox handlers, the renotify policy, both shared closers (an
    answered row and one ended unanswered close through one implementation, and that one reads the
    one open set), and the workflow gate lane's two doors, which close through them rather than
    re-implement the loop with a set of their own (#335). A private copy anywhere keeps the
    members it was written with, and its row answers as before.
    """
    from personalclaw.proactive import collect

    assert collect.ATTENTION_STATUSES is OPEN_STATUSES, "the digest lane must alias the one set"
    assert len(_CONSUMERS) == 6
    for n, (owner, to) in enumerate(_REDEFINED):
        with _open_redefined(to) as mp:
            for i, (consumer, check) in enumerate(_CONSUMERS):
                with row(subtests, owner=owner, consumer=consumer):
                    check(tmp_path / f"{n}-{i}", mp, to)
    assert not _holders(_REDEFINED[0][1]) and not _holders(_REDEFINED[1][1])
    assert inbox_mod.OPEN_STATUSES is OPEN_STATUSES


def test_the_renotify_policy_reads_the_one_open_set():
    """A staleness reminder fires for an open card and stays silent for a resolved one.

    The policy used its own `("pending", "seen")`. A reminder policy that disagreed with the count
    would nag about rows the surface has stopped showing, or go quiet on rows it still shows.
    """
    from personalclaw.workflows.needs_input import (
        RENOTIFY_AFTER_HOURS,
        BlockKind,
        NeedsInputItem,
        should_renotify,
    )

    now = 1_000_000.0
    stale = NeedsInputItem(
        run_id="run-1",
        node_id="node-1",
        block_kind=BlockKind.NEEDS_INPUT,
        blocker="b",
        created_at=now - (RENOTIFY_AFTER_HOURS + 1) * 3600,
    )
    # `"seen"` is named LITERALLY, not taken from OPEN_STATUSES: reading the set under test to
    # build the input makes the assertion vacuous under exactly the mutation that matters (drop SEEN
    # from the owner and a set-derived loop stops testing it).
    assert should_renotify(stale, now=now, status="seen")[0] is True
    assert should_renotify(stale, now=now, status="pending")[0] is True
    assert should_renotify(stale, now=now, status="handled")[0] is False
    for status in sorted(OPEN_STATUSES):
        assert should_renotify(stale, now=now, status=status)[0] is True, status
    for status in sorted(RESOLVED_STATUSES):
        assert should_renotify(stale, now=now, status=status)[0] is False, status


# ── the endpoints ──


def _item(i: int, status: str = ItemStatus.PENDING, kind: str = "proposal") -> InboxItem:
    return InboxItem(
        id=f"{kind}{i}_{i}.000",
        channel="agent",
        channel_name="",
        thread_ts=None,
        message=f"m{i}",
        sender_id=f"s{i}",
        sender_name=f"S{i}",
        classification="fyi",
        status=status,
        created_at=float(i),
        item_kind=kind,
    )


#: The seeded mix the gateway measurement used, shrunk: enough of each status that a wrong
#: definition produces a DIFFERENT number rather than the same one by luck.
def _seed() -> list[InboxItem]:
    return [
        _item(1),
        _item(2),
        _item(3, kind="message"),
        _item(4, status=ItemStatus.SEEN),
        _item(5, status=ItemStatus.SEEN, kind="message"),
        _item(6, status=ItemStatus.HANDLED),
        _item(7, status=ItemStatus.DISMISSED, kind="message"),
        _item(8, status=ItemStatus.FILTERED, kind="system"),
    ]


#: open = 5 (three PENDING + two SEEN); pending-only would be 3. The two numbers differ, which is
#: what makes every assertion below able to fail.
_OPEN = 5
_PENDING_ONLY = 3


def _dashboard(home, monkeypatch, items):
    """A dashboard state over stores in *home* holding *items*, with the config home isolated.

    `api_inbox_status` calls `AppConfig.load()`; pointing `config_dir` at *home* keeps this
    suite off the real `~/.personalclaw` even for the read.
    """
    import personalclaw.config.loader as loader

    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(loader, "config_dir", lambda: home)
    inbox = InboxStore(path=home / "inbox.json")
    for it in items:
        inbox.add(it)
    inbox_state = InboxState(path=home / "inbox_state.json")
    svc = SimpleNamespace(
        state=inbox_state,
        inbox=inbox,
        health=lambda: {"running": True, "stale": False},
        sorter=SimpleNamespace(wake=lambda: None),
    )
    st = MagicMock()
    st._inbox_svc = svc
    st.broadcast_ws = lambda *a, **k: None
    return st


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A dashboard state over the seeded stores on tmp_path."""
    return _dashboard(tmp_path, monkeypatch, _seed())


def _get(state, path: str) -> dict | list:
    app = web.Application()
    app["state"] = state
    resp = asyncio.run(getattr(H, _HANDLERS[path])(make_mocked_request("GET", path, app=app)))
    assert resp.status == 200
    return json.loads(resp.body)


_HANDLERS = {
    "/api/inbox": "api_inbox_list",
    "/api/inbox/open": "api_inbox_open_list",
    "/api/inbox/kinds": "api_inbox_kinds",
    "/api/inbox/owners": "api_inbox_owners",
    "/api/inbox/status": "api_inbox_status",
}


def _glance(state, item_id: str) -> None:
    """Exactly what opening an item in the UI does: `POST /api/inbox/seen` for that one id.

    Nothing is resolved by this — it is the read/unread boundary moving, and no more.
    """
    app = web.Application()
    app["state"] = state
    req = make_mocked_request("POST", "/api/inbox/seen", app=app)

    async def _body():
        return {"ids": [item_id]}

    req.json = _body  # type: ignore[method-assign]
    resp = asyncio.run(H.api_inbox_seen(req))
    assert resp.status == 200
    assert json.loads(resp.body)["seen"] == 1, "the glance must actually have marked the row SEEN"


def test_status_publishes_one_open_count_and_no_pending_count(state):
    """One count on the wire, and it counts the open set.

    `pending_count` is GONE rather than published alongside — two counts is what licensed a header
    and a filter row to describe different sets while both looked authoritative.
    """
    body = _get(state, "/api/inbox/status")
    assert body["open_count"] == _OPEN
    assert body["open_count"] != _PENDING_ONLY, "the count must not be the PENDING-only one"
    assert "pending_count" not in body, (
        "publishing both counts is the defect, not the fix — a second count is a second definition "
        "waiting for a second surface to render it"
    )
    assert body["total_count"] == len(_seed())


def test_a_glance_does_not_change_the_open_count(state):
    """🔴 THE SECOND HALF OF THE BUG. Opening an item must not move the count.

    Measured before the fix, against a real gateway: 33 → 32 on a single open, nothing resolved.
    A number that falls because the user looked at something teaches them the number is noise.
    """
    before = _get(state, "/api/inbox/status")["open_count"]
    _glance(state, _item(1).id)
    after = _get(state, "/api/inbox/status")["open_count"]
    assert after == before == _OPEN, (
        f"the header moved {before} → {after} on a mere glance — attention state must change when "
        "the user ACTS, not when they read"
    )


def test_the_open_endpoint_returns_seen_rows_and_survives_a_glance(state):
    """`GET /api/inbox/open` feeds Mission Control, the Action Center and the companion.

    It was `/api/inbox/pending` and excluded SEEN, so a glance in the inbox deleted the row from the
    dashboard's lanes — while `attentionLanes.STATUS_OPEN.seen === true` on the very surface being
    fed. 33 rows, then 32 after one open.
    """
    rows = _get(state, "/api/inbox/open")
    assert len(rows) == _OPEN
    assert {r["status"] for r in rows} == set(OPEN_STATUSES), "a SEEN row must reach the dashboard"
    _glance(state, _item(1).id)
    assert len(_get(state, "/api/inbox/open")) == _OPEN, "a glance must not empty the lanes"


def test_kind_chip_counts_use_the_one_open_set(state):
    """The per-kind `open` counts, and their immunity to a glance.

    The chips were already right (`pending|seen`); they are pinned so the fix cannot be "make the
    filters agree with the header" — the wrong direction, since it would hide read-but-unanswered
    work everywhere instead of only in the header.
    """
    by_kind = {k["kind"]: k for k in _get(state, "/api/inbox/kinds")["kinds"]}
    assert by_kind["proposal"]["open"] == 3  # two PENDING + one SEEN
    assert by_kind["message"]["open"] == 2  # one PENDING + one SEEN
    assert by_kind["system"]["open"] == 0  # FILTERED only
    assert by_kind["proposal"]["total"] == 4
    _glance(state, _item(1).id)
    after = {k["kind"]: k["open"] for k in _get(state, "/api/inbox/kinds")["kinds"]}
    assert after["proposal"] == 3 and after["message"] == 2


def test_the_header_count_agrees_with_every_filter_on_the_same_screen(state):
    """The whole issue in one assertion: the numbers a user sees side by side must match.

    `open_count` from `/api/inbox/status`, the Open-filter count the page derives from
    `/api/inbox`, the sum of the kind chips, and the length of `/api/inbox/open` are four
    independent computations of one fact. Before the fix the first was 33 and the rest were 37.
    """
    status = _get(state, "/api/inbox/status")
    items = _get(state, "/api/inbox")
    kinds = _get(state, "/api/inbox/kinds")["kinds"]
    filter_open = sum(1 for i in items if (i["status"] or "pending") in OPEN_STATUSES)
    chips_open = sum(k["open"] for k in kinds)
    feed_open = len(_get(state, "/api/inbox/open"))
    assert status["open_count"] == filter_open == chips_open == feed_open == _OPEN


def test_dismiss_all_sweeps_exactly_what_the_status_count_promised(state):
    """The confirm's blast radius is the sweep's blast radius.

    The dialog is sized from `open_count`; the endpoint sweeps `open_items()`. Sized from the old
    `pending_count`, the confirm said 33 and the endpoint answered `{"dismissed": 37}` — the most
    destructive control on the page understating itself by every row the user had read.
    """
    promised = _get(state, "/api/inbox/status")["open_count"]
    app = web.Application()
    app["state"] = state
    resp = asyncio.run(
        H.api_inbox_dismiss_all(make_mocked_request("POST", "/api/inbox/dismiss-all", app=app))
    )
    assert resp.status == 200
    assert json.loads(resp.body)["dismissed"] == promised == _OPEN
    assert _get(state, "/api/inbox/status")["open_count"] == 0


def test_gate_resolution_still_closes_a_seen_row(tmp_path):
    """`attention.resolve_gate_item` lost its private `_OPEN_STATUSES`; a SEEN row still closes.

    The resolver and the count must agree about "open" by construction: a resolver with a narrower
    set would leave a row that the surface still counts, and a wider one would overwrite an answer
    the user already gave.
    """
    from personalclaw.workflows import attention

    store = InboxStore(path=tmp_path / "inbox.json")
    # `enumerate`, not `hash(status)`: `PYTHONHASHSEED` is randomised per interpreter, so a
    # hash-derived id collides mod 1000 in ~0.5% of processes, two rows share a key in the
    # id-keyed store, one overwrites the other and `closed == 2` fails as `1 == 2` (#3356).
    for i, status in enumerate((ItemStatus.PENDING, ItemStatus.SEEN, ItemStatus.DISMISSED)):
        it = _item(i, status=status, kind="needs_input")
        it.refs = {"workflow": "run-1"}
        store.add(it)
    st = SimpleNamespace(_inbox_svc=SimpleNamespace(inbox=store, state=None))
    closed = attention.resolve_gate_item(st, "run-1")
    assert closed == 2, "both the PENDING and the SEEN row are closeable; the DISMISSED one is not"
    assert sorted(i.status for i in store.items.values()) == [
        ItemStatus.DISMISSED.value,
        ItemStatus.HANDLED.value,
        ItemStatus.HANDLED.value,
    ]
