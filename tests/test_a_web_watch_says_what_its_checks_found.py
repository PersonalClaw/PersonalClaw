"""A web watch's checks are on the record: a watch that cannot fire says so, and is reported once.

Measured on the release before this, on a watch of a page on this computer: three checks refused by
the network settings, then, with the host allowed, a first check that found nothing on the page to
track, so the watch could never fire. Through all of it the Triggers page read "Firing on its own ·
No runs recorded yet", `GET /api/triggers` answered `run_count: 0, last_error: "", warnings: []`,
the gateway log had one INFO line per check (below the owner's level) and the Inbox had nothing,
though the automation's failures were routed there. The poll returned each reason "so the caller
can write the ledger rows", and its one caller logged it and let it go.

Now each check leaves the watch's last check: what it came to, in words, and how many checks in a
row came to it. A watch that cannot fire carries a warning on every surface that lists it, and the
first check of a stretch it cannot fire through is reported once, on the trigger's failure route.

The watched page is on this computer. The egress guard refuses it before any connection is made,
so the refused checks below run through the real chokepoint; a page a check reads is handed in.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import triggers as T
from personalclaw.inbox import InboxStore
from personalclaw.instants import utc_iso
from personalclaw.triggers import tools, web_poll
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0
WATCH_ID = "web_watch:release-watch"
URL = "http://localhost:18999/releases"
#: A releases page with no links and no feed entries: the shape the watch could not track.
PAGE = "<html><body><h1>Releases</h1><p>0.28.1, 6 December 2024</p></body></html>"
FEED = "<rss><item><guid>release-0.28.0</guid></item><item><guid>release-0.28.1</guid></item></rss>"
ALLOWED_HOSTS_STEP = "add localhost to Allowed hosts in Settings → Security → Network egress"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A tmp home the poll, the Inbox and the handler all read."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    monkeypatch.setattr(T, "_hook_store", lambda s: SimpleNamespace(list_all=list, load=list))
    monkeypatch.setattr(T, "_used_by_index", lambda: {})
    return tmp_path


class _State:
    """The dashboard state a report goes through: the notes it raised, the refreshes it pushed."""

    def __init__(self) -> None:
        self.notes: list[dict[str, Any]] = []
        self.refreshes: list[tuple[str, ...]] = []

    def notify(self, kind, title, body, *, meta=None, raised_by_app=""):
        self.notes.append({"kind": kind, "title": title, "body": body, "meta": dict(meta or {})})

    def broadcast_ws(self, *_a: Any, **_k: Any) -> None:
        return None

    def push_refresh(self, *kinds: str) -> None:
        self.refreshes.append(kinds)


def _watch(home, *, url: str = URL, **fields: Any) -> Any:
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=WATCH_ID,
            name="release watch",
            kind="web_watch",
            enabled=True,
            spec={"url": url},
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
            **fields,
        )
    )
    return TriggerStore(base_dir=home).get(WATCH_ID).trigger


def _page(body: str):
    def fetch(url):
        return SimpleNamespace(status=200, body=body.encode(), url=url, headers={}, truncated=False)

    return fetch


def _inbox_items() -> list[Any]:
    store = InboxStore()
    store.load()
    return list(store.items.values())


def _run_the_loop(monkeypatch, state: _State, *, passes: int) -> None:
    """The gateway's own poll loop, for `passes` passes `poll_interval` apart, then stopped."""
    import personalclaw.gateway as gw

    clock = iter(NOW + i * 400 for i in range(passes))
    slept = {"n": 0}

    async def sleep(_secs: float) -> None:
        if slept["n"] == passes:
            raise asyncio.CancelledError
        slept["n"] += 1

    monkeypatch.setattr(
        gw,
        "asyncio",
        SimpleNamespace(
            sleep=sleep, to_thread=asyncio.to_thread, CancelledError=asyncio.CancelledError
        ),
    )
    monkeypatch.setattr(gw, "time", SimpleNamespace(time=lambda: next(clock)))
    orchestrator = object.__new__(gw.GatewayOrchestrator)
    orchestrator.dashboard_state = state
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(orchestrator._web_watch_poll_loop())


def _listed(home) -> dict[str, Any]:
    app = web.Application()
    app["state"] = SimpleNamespace(_hook_store=None)
    request = make_mocked_request("GET", "/api/triggers", app=app)
    request["user"] = "tester"
    data = json.loads(asyncio.run(T.api_triggers(request)).body.decode())
    return next(t for t in data["triggers"] if t["id"] == f"store:{WATCH_ID}")


# ── each check leaves a record ──


def test_a_check_the_network_settings_refuse_names_the_setting_that_lifts_it(home):
    """🔴 Before: the refusal was an INFO line and nothing else. The sentence is the guard's own,
    ending in what a watch does once the host is allowed: it has no Test button to press."""
    trigger = _watch(home)
    outcome = web_poll.poll_one(trigger, now=NOW, base_dir=home)

    assert outcome.check == web_poll.WatchCheck.REFUSED
    assert ALLOWED_HOSTS_STEP in outcome.said
    assert "its next check reaches it" in outcome.said
    assert "test again" not in outcome.said
    assert "It cannot fire while its checks are refused." in outcome.said
    check = web_poll.last_check(trigger, base_dir=home)
    assert check is not None
    assert (check["outcome"], check["can_fire"], check["checks"]) == ("refused", False, 1)
    assert check["said"] == outcome.said


def test_a_first_check_that_finds_nothing_to_watch_says_the_watch_cannot_fire(home):
    """🔴 Before: "seeded 0 item(s)", then "no new items" on every check after, which reads like a
    watch that works. A page with nothing to track is not a page with nothing new."""
    trigger = _watch(home)
    first = web_poll.poll_one(trigger, now=NOW, base_dir=home, fetcher=_page(PAGE))

    assert first.check == web_poll.WatchCheck.EMPTY
    assert f"It read {URL} and found nothing it can watch" in first.said
    assert "feed entries (RSS or Atom)" in first.said
    assert "Point it at the page's feed" in first.said
    later = web_poll.poll_one(trigger, now=NOW + 400, base_dir=home, fetcher=_page(PAGE))
    assert later.check == web_poll.WatchCheck.EMPTY, "never 'nothing new' for a page like this"
    check = web_poll.last_check(trigger, base_dir=home)
    assert check is not None
    assert (check["can_fire"], check["checks"], check["items"]) == (False, 2, 0)


def test_a_watch_that_found_its_items_can_fire(home):
    """The vacuity floor: a working watch is not flagged, or people learn to skip the warning."""
    trigger = _watch(home)
    seed = web_poll.poll_one(trigger, now=NOW, base_dir=home, fetcher=_page(FEED))
    assert seed.check == web_poll.WatchCheck.SEEDED
    assert seed.said == (
        "Its first check recorded the 2 items on the page; it fires when a new one appears."
    )
    check = web_poll.last_check(trigger, base_dir=home)
    assert check is not None and check["can_fire"] is True and check["items"] == 2


def test_checks_in_a_row_are_one_stretch_counted_from_its_start(home):
    """One stretch, so the page can say "3 checks since …" and the report is owed once."""
    trigger = _watch(home)
    outcomes = [web_poll.poll_one(trigger, now=NOW + i * 400, base_dir=home) for i in range(3)]

    assert [o.started for o in outcomes] == [True, False, False]
    assert [o.owed for o in outcomes] == [True, True, True], "owed until it is reported"
    web_poll.mark_reported(trigger.id, base_dir=home)
    assert web_poll.poll_one(trigger, now=NOW + 1200, base_dir=home).owed is False
    check = web_poll.last_check(trigger, base_dir=home)
    assert check is not None
    assert check["checks"] == 4
    assert (check["since"], check["at"]) == (utc_iso(NOW), utc_iso(NOW + 1200))


def test_a_new_outcome_starts_a_new_stretch_owed_its_own_report(home):
    """Allowing the host ends the refusals; a page with nothing to track is a new problem."""
    trigger = _watch(home)
    web_poll.poll_one(trigger, now=NOW, base_dir=home)
    web_poll.mark_reported(trigger.id, base_dir=home)
    empty = web_poll.poll_one(trigger, now=NOW + 400, base_dir=home, fetcher=_page(PAGE))
    assert (empty.check, empty.started, empty.owed) == ("empty", True, True)


def test_a_check_of_an_address_the_watch_no_longer_has_says_nothing_about_it(home):
    """Its refusals were about the old address; the new one has not been checked yet."""
    trigger = _watch(home)
    web_poll.poll_one(trigger, now=NOW, base_dir=home)
    moved = _watch(home, url="https://releases.example/feed.xml")
    assert web_poll.last_check(moved, base_dir=home) is None


def test_a_page_that_gains_items_after_an_empty_check_seeds_them(home):
    """An empty check is not the seed. It was, so the first check that found items delivered every
    one of them as new: the burst seeding exists to prevent."""
    trigger = _watch(home)
    web_poll.poll_one(trigger, now=NOW, base_dir=home, fetcher=_page(PAGE))
    seed = web_poll.poll_one(trigger, now=NOW + 400, base_dir=home, fetcher=_page(FEED))
    assert (seed.check, seed.payload) == ("seeded", None)
    newer = FEED.replace("</rss>", "<item><guid>release-0.29.0</guid></item></rss>")
    fired = web_poll.poll_one(trigger, now=NOW + 800, base_dir=home, fetcher=_page(newer))
    assert fired.check == web_poll.WatchCheck.FIRED
    assert fired.payload is not None and fired.payload["new_count"] == 1


def test_a_watch_pointed_at_another_page_seeds_it_rather_than_firing_all_of_it(home):
    """What the empty check advises — point it at the page's feed — must not end in a fire for
    every item the feed already had."""
    trigger = _watch(home)
    web_poll.poll_one(trigger, now=NOW, base_dir=home, fetcher=_page(FEED))
    moved = _watch(home, url="https://releases.example/feed.xml")
    other = "<rss>" + "".join(f"<item><guid>{g}</guid></item>" for g in "abc") + "</rss>"
    seed = web_poll.poll_one(moved, now=NOW + 400, base_dir=home, fetcher=_page(other))
    assert (seed.check, seed.payload) == ("seeded", None)
    assert "recorded the 3 items" in seed.said


def test_a_check_that_cannot_run_is_recorded_too(home, monkeypatch):
    """A poll that raised used to leave a "the poll raised" row for a ledger nobody wrote."""
    trigger = _watch(home)

    def broken(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(web_poll, "poll_one", broken)
    _payloads, checks = web_poll.poll_all(TriggerStore(base_dir=home), now=NOW, base_dir=home)
    [(checked, outcome)] = checks
    assert checked.id == trigger.id
    assert outcome.check == web_poll.WatchCheck.FAILED
    assert "Its check could not run (RuntimeError)" in outcome.said


# ── what the gateway does with them ──


def test_the_gateway_files_a_refused_watch_in_the_inbox_once(home, monkeypatch, caplog):
    """🔴 Before: three refused checks, nothing in the Inbox, nothing in the log at her level."""
    _watch(home)
    state = _State()
    caplog.set_level(logging.WARNING, logger="personalclaw.triggers.web_poll")
    _run_the_loop(monkeypatch, state, passes=3)

    [item] = _inbox_items()
    assert item.message.startswith("release watch failed\n\n")
    assert ALLOWED_HOSTS_STEP in item.message
    assert item.refs["trigger"] == WATCH_ID
    [note] = state.notes
    assert note["meta"]["statusUrl"] == f"#/triggers?open={WATCH_ID}"
    logged = [r for r in caplog.records if "cannot fire" in r.getMessage()]
    assert len(logged) == 1, "one line for the stretch, not one per check"
    assert state.refreshes == [("crons",)] * 3, "the Triggers page re-reads after every pass"
    check = web_poll.last_check(TriggerStore(base_dir=home).get(WATCH_ID).trigger, base_dir=home)
    assert check is not None and check["checks"] == 3


def test_a_watch_whose_failures_are_muted_files_nothing(home, monkeypatch):
    """`failure_delivery: none` is the owner's "don't tell me", and a check honours it as a run's
    failure does; the row's warning still says it cannot fire."""
    _watch(home, failure_delivery="none")
    state = _State()
    _run_the_loop(monkeypatch, state, passes=2)
    assert _inbox_items() == [] and state.notes == []
    assert _listed(home)["warnings"], "muted is not hidden: the page still says it"


# ── what the surfaces say ──


def test_the_list_carries_the_last_check_and_the_warning(home):
    """🔴 Before: `warnings: []` and no trace of a check on the wire."""
    trigger = _watch(home)
    assert _listed(home)["last_check"] is None, "not checked yet"
    web_poll.poll_one(trigger, now=NOW, base_dir=home)

    row = _listed(home)
    assert row["last_check"]["outcome"] == "refused"
    assert row["last_check"]["can_fire"] is False
    assert row["warnings"] == [row["last_check"]["said"]]
    assert ALLOWED_HOSTS_STEP in row["warnings"][0]


def test_the_list_does_not_warn_about_a_working_watch(home):
    trigger = _watch(home)
    web_poll.poll_one(trigger, now=NOW, base_dir=home, fetcher=_page(FEED))
    row = _listed(home)
    assert row["last_check"]["outcome"] == "seeded" and row["last_check"]["can_fire"] is True
    assert row["warnings"] == []


def test_the_agents_list_says_the_watch_cannot_fire(home):
    """The agent answers "is my watch working?" from this list; it read `health=ok`."""
    trigger = _watch(home)
    web_poll.poll_one(trigger, now=NOW, base_dir=home, fetcher=_page(PAGE))
    result = tools.list_automations(TriggerStore(base_dir=home))
    assert f"⚠ cannot fire: It read {URL} and found nothing it can watch" in result.text
    [row] = result.data["automations"]
    assert row["last_check"]["can_fire"] is False


def test_creating_a_watch_says_how_it_checks_and_where_a_check_it_cannot_make_goes(home):
    """The automation was created with its failures routed to the Inbox, and the agent told the
    owner so; the reply now says what a check does, in the words of the route it reports on."""
    result = tools.create(
        TriggerStore(base_dir=home),
        name="release watch",
        kind="web_watch",
        spec={"url": URL},
        message="Tell me when 0.29 is listed.",
    )
    assert result.ok, result.text
    assert (
        "it checks the page every 5 minutes: its first check only records what is there, and a "
        "new item after that runs it"
    ) in result.text
    assert (
        "if a check is refused, cannot read the page or finds nothing on it to watch: filed in "
        "your Inbox, once until that changes"
    ) in result.text


def test_a_watch_whose_failures_are_muted_is_not_promised_a_report(home):
    """ "you are not told, once until that changes" would promise something and take it back."""
    trigger = _watch(home, failure_delivery="none")
    [_how, where] = tools._watch_said(trigger, chat_channels=None)
    assert where == (
        "if a check is refused, cannot read the page or finds nothing on it to watch: you are not "
        "told, and the Triggers page shows it"
    )
