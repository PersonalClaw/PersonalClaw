"""One damaged web-watch sidecar stops only its own watch, which says why.

Each web watch keeps what it has seen, its request counts and its last check in a sidecar file. A
sidecar holding a byte that is not UTF-8, or a field of the wrong type (`"requests_today": "x"`),
made `load_state()` raise (it caught only `OSError` and `JSONDecodeError`), and `poll_all()`'s
fallback called it again outside any guard. So the error left `poll_all()` on every pass: every
watch after the bad one was never polled, and the watches before it had their new items marked
seen and their fires thrown away with the pass, while each fire was counted.

A sidecar that is there and cannot be read is now kept aside as a copy (`record_files`), and its
watch records one failed check that says so and starts over: its next check looks afresh and fires
on nothing it finds then. A record it cannot save over either leaves it checking nothing until it
can, which it says once and the page reads while it lasts, trying again ever more slowly. A field
of the wrong type reads as its default. Every other watch polls and fires as before.
"""

from __future__ import annotations

import json
import types
from typing import Any

import pytest

from personalclaw import record_files
from personalclaw.triggers import web_poll
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


def _watch(store: TriggerStore, slug: str) -> str:
    tid = f"web_watch:{slug}"
    store.upsert(
        Trigger(
            id=tid,
            name=slug,
            kind="web_watch",
            enabled=True,
            spec={"url": f"https://example.com/{slug}", "poll_interval": 300},
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    return tid


class _Feeds:
    """Each watched page's feed, by its address, which a test adds items to."""

    def __init__(self) -> None:
        self.items: dict[str, list[str]] = {}

    def add(self, slug: str, guid: str) -> None:
        self.items.setdefault(f"https://example.com/{slug}", []).append(guid)

    def __call__(self, url: str) -> Any:
        guids = "".join(f"<item><guid>{g}</guid></item>" for g in self.items.get(url, []))
        body = f"<rss>{guids}</rss>".encode()
        return types.SimpleNamespace(status=200, body=body, url=url, headers={}, truncated=False)


class _Knowledge:
    def create_typed_item(self, **kwargs: Any) -> str:
        return "item-1"


def _poll(store: TriggerStore, home, feeds: _Feeds, *, at: float):
    return web_poll.poll_all(
        store, now=at, base_dir=home, fetcher=feeds, knowledge_store=_Knowledge()
    )


def _sidecar(home, tid: str):
    return web_poll._state_path(tid, home)


def _seeded_pair(home):
    """Two watches, `a` then `b`, both seeded on their first look."""
    store = TriggerStore(base_dir=home)
    first, second = _watch(store, "a"), _watch(store, "b")
    feeds = _Feeds()
    feeds.add("a", "a-1")
    feeds.add("b", "b-1")
    payloads, _ = _poll(store, home, feeds, at=NOW)
    assert payloads == [], "a first look fires nothing"
    return store, feeds, first, second


def _wrong_type(record: bytes) -> bytes:
    """The watch's own record, intact but for one field of the wrong type."""
    fields = json.loads(record)
    fields["requests_today"] = "x"
    return json.dumps(fields).encode()


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(
            lambda _record: b'{"seeded": true, "seen": ["a-1"], "day": "\xff"}', id="not-utf-8"
        ),
        pytest.param(_wrong_type, id="wrong-type"),
    ],
)
def test_the_watch_after_a_damaged_one_still_polls_and_fires(home, damage):
    store, feeds, first, second = _seeded_pair(home)
    path = _sidecar(home, first)
    path.write_bytes(damage(path.read_bytes()))
    feeds.add("b", "b-2")

    payloads, checks = _poll(store, home, feeds, at=NOW + 3600)

    assert [c for t, c in checks if t.id == second][0].check == web_poll.WatchCheck.FIRED.value
    assert len(payloads) == 1, "the healthy watch's new item fired"


def test_a_sidecar_that_cannot_be_read_is_kept_aside_and_its_watch_says_why(home):
    store, feeds, first, _second = _seeded_pair(home)
    path = _sidecar(home, first)
    path.write_bytes(b'{"seeded": true, "seen": ["a-1"], "day": "\xff"}')

    _payloads, checks = _poll(store, home, feeds, at=NOW + 3600)

    (outcome,) = [c for t, c in checks if t.id == first]
    assert outcome.check == web_poll.WatchCheck.FAILED.value
    assert "could not be read" in outcome.said and "starts over" in outcome.said, outcome.said
    kept = record_files.kept_copies(path)
    assert len(kept) == 1 and kept[0].name in outcome.said, "the damaged file is kept for a look"
    # It starts over: its next check looks afresh, and its first look fires nothing.
    feeds.add("a", "a-2")
    payloads, checks = _poll(store, home, feeds, at=NOW + 7200)
    (again,) = [c for t, c in checks if t.id == first]
    assert again.check != web_poll.WatchCheck.FAILED.value
    assert not [p for p in payloads if first in json.dumps(p, default=str)]


def test_a_text_false_reads_as_not_seeded():
    assert web_poll.WatchState.from_dict({"seeded": "false"}).seeded is False
    assert web_poll.WatchState.from_dict({"seeded": True}).seeded is True


def test_a_field_of_the_wrong_type_reads_as_its_default():
    state = web_poll.WatchState.from_dict(
        {
            "seeded": True,
            "requests_today": "x",
            "last_polled_at": [],
            "check": {"outcome": "fired", "items": "many"},
        }
    )
    assert (state.requests_today, state.last_polled_at, state.check.items) == (0, 0.0, 0)


def test_the_watch_quotes_its_own_words_not_the_stores(home):
    """The store's sentence says nothing is written to the file and asks for a repair; this watch
    writes a fresh record over it, so it says what it does instead."""
    store, feeds, first, _second = _seeded_pair(home)
    _sidecar(home, first).write_bytes(b'{"seeded": true, "day": "\xff"}')

    _payloads, checks = _poll(store, home, feeds, at=NOW + 3600)

    (outcome,) = [c for t, c in checks if t.id == first]
    assert (
        "Repair the file" not in outcome.said and "Nothing is written" not in outcome.said
    ), outcome.said
    assert "kept beside it" in outcome.said, outcome.said


def _neither_read_nor_saved(path) -> None:
    """Where the watch's record goes there is a directory: it can be neither read nor saved over."""
    if path.is_file():
        path.unlink()
    path.mkdir()


def _tries(
    store, home, feeds, tid: str, *, start: float, minutes: int
) -> list[tuple[float, float]]:
    """Poll every minute after *start*: each time the watch tried its record, and the wait it took
    after (`web_poll._UNREADABLE`). A watch waiting out its wait does not look at its record."""
    record = str(_sidecar(home, tid))
    tries: list[tuple[float, float]] = []
    last = None
    for minute in range(1, minutes + 1):
        at = start + 60 * minute
        _poll(store, home, feeds, at=at)
        held = web_poll._UNREADABLE.get(record)
        if held is not None and held.look_at != last:
            tries.append((at, held.wait))
            last = held.look_at
    return tries


def test_a_record_that_can_be_neither_read_nor_saved_says_what_it_does_once_and_on_the_page(home):
    """A record that cannot be opened cannot be written over either, so its watch said it could not
    be read on every poll, forever. It says so once per reason, says what it does instead of
    starting over, which it cannot, and the Automations page reads the same while it lasts: it read
    only the record, so the watch showed as firing on its own."""
    store, feeds, first, _second = _seeded_pair(home)
    _neither_read_nor_saved(_sidecar(home, first))
    failed: list[str] = []
    for minute in range(1, 61):
        _payloads, checks = _poll(store, home, feeds, at=NOW + 3600 + 60 * minute)
        failed += [
            c.said
            for t, c in checks
            if t.id == first and c.check == web_poll.WatchCheck.FAILED.value
        ]

    assert len(failed) == 1, f"said {len(failed)} times in an hour"
    (said,) = failed
    assert "could not be saved over it" in said and "checks nothing" in said, said
    assert "starts over" not in said and "afresh" not in said, said
    shown = web_poll.last_check(store.get(first).trigger, base_dir=home)
    assert shown is not None, "the page reads no check: the watch shows as firing on its own"
    assert shown["can_fire"] is False and shown["said"] == said, shown


def test_it_tries_again_ever_more_slowly_up_to_half_an_hour_and_never_stops(home):
    store, feeds, first, _second = _seeded_pair(home)
    _neither_read_nor_saved(_sidecar(home, first))

    tries = _tries(store, home, feeds, first, start=NOW + 3600, minutes=240)

    waits = [wait for _at, wait in tries]
    assert waits[:6] == [60, 120, 240, 480, 960, 1800], waits
    assert len(waits) > 7 and set(waits[6:]) == {1800}, f"it stopped trying: {waits}"
    for (at, wait), (next_at, _wait) in zip(tries, tries[1:]):
        assert at + wait <= next_at < at + wait + 60, "a try came before its wait was up, or late"


def test_a_new_reason_is_said_again_and_waits_from_a_minute(home):
    store, feeds, first, _second = _seeded_pair(home)
    path = _sidecar(home, first)
    _neither_read_nor_saved(path)
    tries = _tries(store, home, feeds, first, start=NOW + 3600, minutes=20)
    at, wait = tries[-1]
    assert wait > 60
    # Now it can be saved over, and cannot be read for another reason.
    path.rmdir()
    path.write_bytes(b'{"seeded": true, "seen": ["a-1"], "day": "\xff"}')

    _payloads, checks = _poll(store, home, feeds, at=at + wait)

    (outcome,) = [c for t, c in checks if t.id == first]
    assert outcome.check == web_poll.WatchCheck.FAILED.value and outcome.owed, outcome
    assert "starts over" in outcome.said, outcome.said
    assert web_poll._UNREADABLE[str(path)].wait == 60


def test_a_record_read_again_ends_the_wait(home):
    store, feeds, first, _second = _seeded_pair(home)
    path = _sidecar(home, first)
    _neither_read_nor_saved(path)
    tries = _tries(store, home, feeds, first, start=NOW + 3600, minutes=20)
    at, wait = tries[-1]
    path.rmdir()  # the record is gone: the watch reads a fresh one, and looks at the page

    _payloads, checks = _poll(store, home, feeds, at=at + wait)

    (outcome,) = [c for t, c in checks if t.id == first]
    assert outcome.check == web_poll.WatchCheck.SEEDED.value, outcome
    assert str(path) not in web_poll._UNREADABLE
    shown = web_poll.last_check(store.get(first).trigger, base_dir=home)
    assert shown is not None and shown["outcome"] == web_poll.WatchCheck.SEEDED.value, shown
    # Damaged again, it is said again, and waits from a minute.
    _neither_read_nor_saved(path)
    _payloads, checks = _poll(store, home, feeds, at=at + wait + 600)
    (again,) = [c for t, c in checks if t.id == first]
    assert again.check == web_poll.WatchCheck.FAILED.value and again.owed, again
    assert web_poll._UNREADABLE[str(path)].wait == 60
