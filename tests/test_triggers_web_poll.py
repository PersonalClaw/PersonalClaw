"""The web_watch poll runtime — what actually FIRES a `web_watch` trigger (§7 item 8 — S121).

🔴 THE DEFECT. `web_watch` was a fully declared kind with no runtime. It is in `KINDS`, `SPEC_KEYS`
accepts `{url, poll_interval, extraction, novelty_key}`, `nl_kind.route()` routes any URL to it, the
store persists it, `/api/triggers` lists it (S94) and the Automations page renders it (S95). Nothing
polled it. Measured before a line was written:

    T.create(store, name="watch pypi", when="watch https://pypi.org/... for changes")
      → ok: True   "Created automation 'watch pypi' (web_watch:watch-pypi), kind web_watch."

    tick()                     → considered: none    (no `next_fire_at`; not a clock kind)
    file_poll.file_triggers()  → ['file:t']          (only `file`)

So a user could ask for exactly what the plan advertises, be told it worked, see it listed in the
UI — and it would never fire. The same shape as S93's file-watch gap, one kind over.

Every test injects its `fetcher`, so nothing here makes a network request. The novelty, budget and
seeding logic is driven for real against a real store on `tmp_path`.
"""

from __future__ import annotations

import types

import pytest

from personalclaw.triggers import web_poll
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0

FEED_TWO = "<rss><item><guid>post-1</guid></item><item><guid>post-2</guid></item></rss>"
FEED_THREE = FEED_TWO.replace("</rss>", "<item><guid>post-3</guid></item></rss>")


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


@pytest.fixture(autouse=True)
def _isolate_knowledge(tmp_path_factory, monkeypatch):
    """Point the DEFAULT knowledge-digest routing at a per-test tmp DB.

    A fire now writes fresh items to the knowledge store, and a test that fires without an injected
    `knowledge_store` would otherwise reach `get_knowledge_store()` → the real `~/.personalclaw`
    knowledge.db (config_dir() resolves there when PERSONALCLAW_HOME is unset). Same real-home
    hazard and remedy as conftest's trigger-store fixture. Returned so a test can assert on it; a
    test that passes its own `knowledge_store` is unaffected."""
    from personalclaw.knowledge.store import KnowledgeStore

    kdb = tmp_path_factory.mktemp("pclaw-knowledge") / "k.db"
    kstore = KnowledgeStore(str(kdb))
    monkeypatch.setattr("personalclaw.knowledge.get_knowledge_store", lambda: kstore)
    return kstore


class _SpyKnowledge:
    """Records `create_typed_item` calls so a test can assert the digest lands in KNOWLEDGE."""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def create_typed_item(self, **kwargs):
        self.items.append(kwargs)
        return f"item-{len(self.items)}"


def _shell_fetcher():
    """A real 200 whose body is an empty JS shell — `extract_items` finds nothing, the escalation
    signal (the page builds its content client-side)."""

    def fetch(url):
        return types.SimpleNamespace(
            status=200,
            body=b"<html><body><div id='app'></div></body></html>",
            url=url,
            headers={},
            truncated=False,
        )

    return fetch


def _renderer(html: str, *, ok: bool = True, unavailable: bool = False, error: str = ""):
    """A sync fake standing in for `web.render.render_url` — returns a `RenderResult` directly, so
    `_render_headless` never touches the event loop. `calls` counts invocations."""
    from personalclaw.web.render import RenderResult

    calls = {"n": 0}

    def render(url, *, policy=None):
        calls["n"] += 1
        return RenderResult(ok=ok, url=url, html=html, unavailable=unavailable, error=error)

    render.calls = calls  # type: ignore[attr-defined]
    return render


def _watch(store, *, tid="web_watch:w", url="https://example.com/feed", **spec):
    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="web_watch",
            enabled=True,
            spec={"url": url, "poll_interval": 300, **spec},
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    return store.get(tid).trigger


def _fetcher(pages):
    """A fetcher over a mutable `{"v": body}` so a test can change the page between polls."""

    def fetch(url):
        return types.SimpleNamespace(
            status=200, body=pages["v"].encode(), url=url, headers={}, truncated=False
        )

    return fetch


# ── the gap itself ──


def test_a_web_watch_trigger_is_ENUMERATED(store):
    """🔴 The defect, at its root: nothing enumerated this kind, so nothing could poll it."""
    _watch(store)
    assert [t.id for t in web_poll.web_watch_triggers(store)] == ["web_watch:w"]


def test_a_DISABLED_watch_is_not_polled(store):
    _watch(store, tid="web_watch:off")
    row = store.get("web_watch:off").trigger
    row.enabled = False
    store.upsert(row)
    assert web_poll.web_watch_triggers(store) == []


def test_a_watch_with_NO_URL_is_not_polled(store):
    """It cannot fetch anything, and scanning would be worse than skipping."""
    _watch(store, tid="web_watch:blank", url="")
    assert web_poll.web_watch_triggers(store) == []


def test_other_kinds_are_not_polled_by_this_runtime(store):
    """Disjointness is what makes this an additive cutover: a clock trigger polled here as well as
    ticked by the clock loop would DOUBLE-FIRE, the hazard S100 measured for the clock switch-over.
    """
    store.upsert(
        Trigger(
            id="clock:c",
            name="c",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 60},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    _watch(store)
    assert [t.id for t in web_poll.web_watch_triggers(store)] == ["web_watch:w"]


# ── seeding: the first poll never fires ──


def test_the_FIRST_poll_seeds_without_firing(store, tmp_path):
    """🔴 Firing here would deliver the entire current front page as "new" — the behaviour that
    makes someone delete the automation on day one. Mirrors `file_poll`'s seeding pass."""
    trigger = _watch(store)
    outcome = web_poll.poll_one(
        trigger, now=NOW, base_dir=tmp_path, fetcher=_fetcher({"v": FEED_TWO})
    )
    assert outcome.payload is None
    assert "seeded" in outcome.reason


def test_the_seed_SURVIVES_a_restart(store, tmp_path):
    """Persisted, so a gateway restart does not re-seed and then re-deliver the whole page."""
    trigger = _watch(store)
    web_poll.poll_one(trigger, now=NOW, base_dir=tmp_path, fetcher=_fetcher({"v": FEED_TWO}))
    assert web_poll.load_state(trigger.id, base_dir=tmp_path).seeded is True


# ── novelty: the seen-set IS the storm guard ──


def test_an_UNCHANGED_page_does_not_fire(store, tmp_path):
    trigger = _watch(store)
    pages = {"v": FEED_TWO}
    web_poll.poll_one(trigger, now=NOW, base_dir=tmp_path, fetcher=_fetcher(pages))
    outcome = web_poll.poll_one(trigger, now=NOW + 400, base_dir=tmp_path, fetcher=_fetcher(pages))
    assert outcome.payload is None
    assert outcome.reason == "no new items"


def test_a_NEW_item_fires_and_names_it(store, tmp_path):
    trigger = _watch(store)
    pages = {"v": FEED_TWO}
    web_poll.poll_one(trigger, now=NOW, base_dir=tmp_path, fetcher=_fetcher(pages))
    pages["v"] = FEED_THREE
    outcome = web_poll.poll_one(trigger, now=NOW + 400, base_dir=tmp_path, fetcher=_fetcher(pages))
    assert outcome.payload is not None
    assert outcome.payload["new_count"] == 1
    # FENCED with provenance since S127: the item text came off a third-party page, so the payload
    # carries the marker AND the origin rather than a bare string a later consumer must know to
    # distrust. The item itself is still legible inside the fence.
    (item,) = outcome.payload["new_items"]
    assert "post-3" in item
    assert "untrusted_content" in item
    assert "source_type=web_watch" in item
    assert "source_id=https://example.com/feed" in item


def test_the_SAME_new_item_does_not_fire_TWICE(store, tmp_path):
    """The seen-set's whole job. Without it, every poll after a change re-fires forever."""
    trigger = _watch(store)
    pages = {"v": FEED_TWO}
    web_poll.poll_one(trigger, now=NOW, base_dir=tmp_path, fetcher=_fetcher(pages))
    pages["v"] = FEED_THREE
    assert web_poll.poll_one(
        trigger, now=NOW + 400, base_dir=tmp_path, fetcher=_fetcher(pages)
    ).payload
    assert (
        web_poll.poll_one(
            trigger, now=NOW + 800, base_dir=tmp_path, fetcher=_fetcher(pages)
        ).payload
        is None
    )


def test_THE_STORM_GUARD_a_page_that_changes_every_fetch_never_fires(store, tmp_path):
    """🔴 THE control. A timestamp, a rotating ad or a CSRF token changes the BODY on every fetch.
    Keying novelty on a body hash would turn one watch into a notification every poll — which is
    what §3 means by "the seen-set IS the storm guard"."""
    trigger = _watch(store, tid="web_watch:noisy")
    counter = {"i": 0}

    def noisy(url):
        counter["i"] += 1
        page = f'<html><span>now {counter["i"]}</span><a href="/only-post">p</a></html>'
        return types.SimpleNamespace(
            status=200, body=page.encode(), url=url, headers={}, truncated=False
        )

    fires = [
        web_poll.poll_one(trigger, now=NOW + i * 400, base_dir=tmp_path, fetcher=noisy).payload
        for i in range(6)
    ]
    assert [f for f in fires if f] == [], "a page changing every fetch must never fire"


def test_extraction_prefers_FEED_IDS_over_every_link(store):
    """An RSS feed keyed by every href in its own description HTML would treat a described link as
    an item. The first matching strategy wins, so a feed is keyed by guid."""
    body = (
        '<rss><item><guid>real-1</guid><description><a href="/x">x</a></description></item></rss>'
    )
    assert web_poll.extract_items(body) == ["real-1"]


def test_a_page_with_NO_items_is_not_everything_is_new(store, tmp_path):
    """Nothing matched must mean "no items found", never "the whole page is one new item" — the
    latter fires on any unparseable page, every poll."""
    trigger = _watch(store)
    pages = {"v": "<html><p>no links, no feed</p></html>"}
    web_poll.poll_one(trigger, now=NOW, base_dir=tmp_path, fetcher=_fetcher(pages))
    outcome = web_poll.poll_one(trigger, now=NOW + 400, base_dir=tmp_path, fetcher=_fetcher(pages))
    assert outcome.payload is None


def test_an_INVALID_novelty_key_falls_back_rather_than_breaking_the_watch(store):
    """A bad author-supplied regex must not take the automation offline."""
    assert web_poll.extract_items(FEED_TWO, novelty_key="((((") == ["post-1", "post-2"]


# ── the rate floor and the daily budget ──


def test_the_poll_interval_is_CLAMPED_to_the_floor(store):
    """🔴 This is the one kind that makes requests to SOMEONE ELSE'S server: a 5-second watch is
    abusive to the target and indistinguishable from a scraper. Clamped rather than refused — a user
    who typed 60 wants frequent checks, and refusing leaves the automation dead over a number they
    can barely see. S109 recorded the R1 floor being declared but read by no code; this one is
    enforced at the point of use."""
    assert web_poll.poll_interval_for(_watch(store, poll_interval=5)) == (
        web_poll.MIN_POLL_INTERVAL_SECS
    )


def test_a_LONGER_interval_is_honoured(store):
    assert web_poll.poll_interval_for(_watch(store, poll_interval=3600)) == 3600


def test_a_watch_polled_TOO_SOON_is_not_fetched(store, tmp_path):
    trigger = _watch(store)
    pages = {"v": FEED_TWO}
    web_poll.poll_one(trigger, now=NOW, base_dir=tmp_path, fetcher=_fetcher(pages))
    outcome = web_poll.poll_one(trigger, now=NOW + 10, base_dir=tmp_path, fetcher=_fetcher(pages))
    assert outcome.reason == "not due"
    assert outcome.fetched is False, "a not-due watch must not spend a request"


def test_the_DAILY_BUDGET_refuses_with_a_visible_reason(store, tmp_path):
    """§7 criterion 8: zero silent drops. A watch that stopped polling with no explanation is
    indistinguishable from a broken one."""
    trigger = _watch(store)
    state = web_poll.WatchState(
        seeded=True, day=web_poll._day_of(NOW), requests_today=web_poll.MAX_REQUESTS_PER_DAY
    )
    web_poll.save_state(trigger.id, state, base_dir=tmp_path)
    outcome = web_poll.poll_one(
        trigger, now=NOW + 10_000, base_dir=tmp_path, fetcher=_fetcher({"v": FEED_TWO})
    )
    assert outcome.payload is None
    assert "budget" in outcome.reason
    assert outcome.fetched is False


def test_the_budget_RESETS_the_next_day(store, tmp_path):
    trigger = _watch(store)
    state = web_poll.WatchState(
        seeded=True, day="2020-01-01", requests_today=web_poll.MAX_REQUESTS_PER_DAY
    )
    web_poll.save_state(trigger.id, state, base_dir=tmp_path)
    assert web_poll.budget_remaining(state, now=NOW) == web_poll.MAX_REQUESTS_PER_DAY


def test_a_FAILED_fetch_still_SPENDS_its_request(store, tmp_path):
    """🔴 Deliberate. A failing url that did not count toward the budget would retry forever at full
    rate — the shape that gets a user's IP blocked."""
    trigger = _watch(store)

    def boom(url):
        raise OSError("unreachable")

    web_poll.poll_one(trigger, now=NOW, base_dir=tmp_path, fetcher=boom)
    assert web_poll.load_state(trigger.id, base_dir=tmp_path).requests_today == 1


# ── failure isolation ──


def test_an_UNREACHABLE_page_is_a_reason_not_a_crash(store, tmp_path):
    trigger = _watch(store)

    def boom(url):
        raise OSError("unreachable")

    outcome = web_poll.poll_one(trigger, now=NOW, base_dir=tmp_path, fetcher=boom)
    assert outcome.payload is None
    assert "fetch failed" in outcome.reason


def test_an_HTTP_ERROR_is_reported_not_treated_as_an_empty_page(store, tmp_path):
    """A 404 body parsed as "no items" would silently mean the watch works; it does not."""
    trigger = _watch(store)

    def gone(url):
        return types.SimpleNamespace(status=404, body=b"", url=url, headers={}, truncated=False)

    outcome = web_poll.poll_one(trigger, now=NOW, base_dir=tmp_path, fetcher=gone)
    assert "404" in outcome.reason


def test_ONE_bad_watch_does_not_strand_the_others(store, tmp_path):
    """A poll loop that died on one unreachable host would silently stop every OTHER watch."""
    _watch(store, tid="web_watch:bad", url="https://bad/")
    _watch(store, tid="web_watch:good")
    pages = {"v": FEED_TWO}

    def mixed(url):
        if "bad" in url:
            raise OSError("unreachable")
        return _fetcher(pages)(url)

    payloads, skipped = web_poll.poll_all(store, now=NOW, base_dir=tmp_path, fetcher=mixed)
    assert {row["trigger_id"] for row in skipped} == {"web_watch:bad", "web_watch:good"}
    assert payloads == [], "both are seeding/failing on this pass, neither fires"


def test_poll_all_reports_skips_for_the_LEDGER(store, tmp_path):
    """The caller writes ledger rows from these, which is how a suppressed poll stays visible."""
    _watch(store)
    _payloads, skipped = web_poll.poll_all(
        store, now=NOW, base_dir=tmp_path, fetcher=_fetcher({"v": FEED_TWO})
    )
    assert skipped and "seeded" in skipped[0]["reason"]


def test_a_CORRUPT_sidecar_reads_as_unseeded_rather_than_raising(tmp_path):
    """A poll loop that died on one bad JSON file would stop every other watch on the machine."""
    path = web_poll._state_path("web_watch:w", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert web_poll.load_state("web_watch:w", base_dir=tmp_path).seeded is False


# ── the egress chokepoint + privacy ──


def test_the_DEFAULT_fetcher_is_the_EGRESS_CHOKEPOINT():
    """🔴 A watch pointed at `http://169.254.169.254/` is an SSRF against the machine's own metadata
    service. `net.fetch` is where host classification, private-IP denial, redirect-hop re-checks,
    the byte cap and the timeout already live — a direct fetch here would bypass all of it.

    Asserted on the source because the property is WHICH layer is called, and a behavioural test
    would need a network."""
    import ast
    import inspect

    src = inspect.getsource(web_poll._fetch)
    assert "from personalclaw.net import fetch" in src

    # Parsed rather than grepped: the docstring NAMES `urllib`/`httpx` as the layers this must not
    # use, so a substring check trips on its own prose. The property is which modules the code
    # imports, which is a question about the AST.
    tree = ast.parse(inspect.getsource(web_poll))
    imported = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not (
        imported & {"urllib", "httpx", "requests", "aiohttp", "socket"}
    ), f"web_poll must fetch ONLY through the egress chokepoint; it imports {sorted(imported)}"


def test_the_seen_set_stores_HASHES_not_urls(store, tmp_path):
    """A seen-set of raw urls is a browsing history in a plaintext sidecar that snapshots (S113)
    carry off the machine. The control needs identity, not the value."""
    trigger = _watch(store)
    web_poll.poll_one(trigger, now=NOW, base_dir=tmp_path, fetcher=_fetcher({"v": FEED_TWO}))
    seen = web_poll.load_state(trigger.id, base_dir=tmp_path).seen
    assert seen and all("post-" not in key for key in seen)


def test_the_seen_set_is_BOUNDED(store, tmp_path):
    """Unbounded, it grows forever on a busy feed."""
    trigger = _watch(store)
    many = "".join(f"<item><guid>p{i}</guid></item>" for i in range(web_poll.MAX_SEEN_KEYS + 50))
    web_poll.poll_one(
        trigger, now=NOW, base_dir=tmp_path, fetcher=_fetcher({"v": f"<rss>{many}</rss>"})
    )
    assert len(web_poll.load_state(trigger.id, base_dir=tmp_path).seen) == web_poll.MAX_SEEN_KEYS


def test_the_payload_item_list_is_CAPPED(store, tmp_path):
    """A payload carrying 400 urls is a prompt nobody can afford."""
    trigger = _watch(store)
    web_poll.poll_one(trigger, now=NOW, base_dir=tmp_path, fetcher=_fetcher({"v": FEED_TWO}))
    many = "".join(f"<item><guid>n{i}</guid></item>" for i in range(200))
    outcome = web_poll.poll_one(
        trigger, now=NOW + 400, base_dir=tmp_path, fetcher=_fetcher({"v": f"<rss>{many}</rss>"})
    )
    assert outcome.payload is not None
    assert len(outcome.payload["new_items"]) == 20
    assert outcome.payload["new_count"] == 200, "the COUNT is honest even when the list is capped"


# ── the boot wiring ──


def test_the_gateway_RUNS_the_poll_loop():
    """🔴 The wiring, not the helper. A runtime nothing calls is the inert-control defect this whole
    session exists to close — the very state `web_watch` was in before it."""
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    boot = inspect.getsource(GatewayOrchestrator)
    assert "_web_watch_poll_loop" in boot
    assert "self._web_watch_task = asyncio.create_task" in boot


def test_the_loop_HONOURS_incident_mode():
    """An unattended fire is an unattended fire regardless of what triggered it (S117)."""
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    src = inspect.getsource(GatewayOrchestrator._web_watch_poll_loop)
    assert "incident_active" in src


def test_the_loop_is_CANCELLED_on_shutdown():
    """A task nobody cancels keeps polling a third party after the user stopped the gateway."""
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    src = inspect.getsource(GatewayOrchestrator)
    shutdown = src[src.index("# Stop services") :]
    assert "self._web_watch_task" in shutdown


# ── the headless escalation tier (WF2AUT-7) ──

# A shell page that, once JS runs, exposes a feed the plain fetch never saw.
RENDERED_FEED = "<rss><item><guid>js-post-1</guid></item></rss>"


def test_escalation_is_OFF_by_default_a_shell_page_does_not_escalate(store, tmp_path):
    """Default OFF: a watch that never set `escalate_headless` polls byte-for-byte as before — a
    shell page seeds, the renderer is never touched."""
    trigger = _watch(store)  # no escalate_headless key
    render = _renderer(RENDERED_FEED)
    outcome = web_poll.poll_one(
        trigger, now=NOW, base_dir=tmp_path, fetcher=_shell_fetcher(), renderer=render
    )
    assert render.calls["n"] == 0, "escalation must not run when the watch didn't opt in"
    assert outcome.escalation == ""
    # seeded on an empty shell (no items), and the renderer stayed idle
    assert "seeded 0 item" in outcome.reason


def test_escalation_ON_a_shell_page_renders_and_extracts(store, tmp_path):
    """Opted in + a real 200 whose plain fetch is empty → escalate, re-extract from the post-JS
    HTML, and mark the escalation so it reaches the ledger/payload."""
    trigger = _watch(store, tid="web_watch:js", escalate_headless=True)
    render = _renderer(RENDERED_FEED)
    # first poll seeds (the rendered item is recorded without firing)
    seed = web_poll.poll_one(
        trigger, now=NOW, base_dir=tmp_path, fetcher=_shell_fetcher(), renderer=render
    )
    assert render.calls["n"] == 1
    assert "escalated to headless; extracted 1 item(s)" in seed.escalation
    assert "seeded 1 item" in seed.reason  # the rendered item WAS seen via the headless tier
    # a NEW rendered item now fires, carrying the escalation marker in the payload
    render2 = _renderer(RENDERED_FEED.replace("js-post-1", "js-post-2"))
    fire = web_poll.poll_one(
        trigger, now=NOW + 400, base_dir=tmp_path, fetcher=_shell_fetcher(), renderer=render2
    )
    assert fire.payload is not None
    assert fire.payload["new_count"] == 1
    assert "extracted 1 item(s)" in fire.payload["escalation"]
    assert "js-post-2" in fire.payload["new_items"][0]


def test_escalation_budget_EXHAUSTED_stops_with_a_visible_reason(store, tmp_path):
    """A render is the expensive tier. When its own daily budget is spent, escalation stops and says
    so (a ledger-visible reason) rather than launching a browser it has no budget for."""
    trigger = _watch(store, tid="web_watch:cap", escalate_headless=True, max_headless_requests=1)
    render = _renderer(RENDERED_FEED)
    # poll 1: seeds, spends the single headless render
    web_poll.poll_one(
        trigger, now=NOW, base_dir=tmp_path, fetcher=_shell_fetcher(), renderer=render
    )
    assert render.calls["n"] == 1
    # poll 2: budget spent → refused, visibly, and the renderer is NOT called again
    outcome = web_poll.poll_one(
        trigger, now=NOW + 400, base_dir=tmp_path, fetcher=_shell_fetcher(), renderer=render
    )
    assert render.calls["n"] == 1, "a spent budget must not launch another render"
    assert "headless escalation budget spent" in outcome.escalation
    assert "headless escalation budget spent" in outcome.reason


def test_a_FAILED_render_still_SPENDS_its_budget(store, tmp_path):
    """Charged win-or-lose: a failed render that did not count would retry every interval forever —
    the runaway the plain budget also guards."""
    trigger = _watch(
        store, tid="web_watch:failrender", escalate_headless=True, max_headless_requests=5
    )
    web_poll.poll_one(  # seed
        trigger,
        now=NOW,
        base_dir=tmp_path,
        fetcher=_shell_fetcher(),
        renderer=_renderer("", ok=False, error="boom"),
    )
    st = web_poll.load_state(trigger.id, base_dir=tmp_path)
    assert st.headless_today == 1, "a failed render is accounted like a successful one"
    outcome = web_poll.poll_one(
        trigger,
        now=NOW + 400,
        base_dir=tmp_path,
        fetcher=_shell_fetcher(),
        renderer=_renderer("", ok=False, error="boom again"),
    )
    assert "headless render failed" in outcome.escalation


def test_PLAYWRIGHT_UNAVAILABLE_does_not_escalate_or_crash(store, tmp_path):
    """`render_url`→`unavailable=True` (Playwright absent). No escalation, no crash, no budget spent
    (it can never succeed until installed) — serve the plain result, reason recorded."""
    trigger = _watch(store, tid="web_watch:noplaywright", escalate_headless=True)
    render = _renderer("", ok=False, unavailable=True)
    outcome = web_poll.poll_one(
        trigger, now=NOW, base_dir=tmp_path, fetcher=_shell_fetcher(), renderer=render
    )
    assert outcome.payload is None  # plain shell seeds, no crash
    assert "headless tier unavailable" in outcome.escalation
    assert "install personalclaw[js-render]" in outcome.escalation
    st = web_poll.load_state(trigger.id, base_dir=tmp_path)
    assert st.headless_today == 0, "an unavailable tier is never charged — it can't succeed yet"


# ── digest routing → the KNOWLEDGE store, never memory ──


def test_a_new_item_ROUTES_to_the_knowledge_store(store, tmp_path):
    """The digest lands in the knowledge store as a searchable user item — the injected spy proves a
    genuinely-new item is written, with web_watch provenance."""
    trigger = _watch(store)
    spy = _SpyKnowledge()
    pages = {"v": FEED_TWO}
    web_poll.poll_one(
        trigger, now=NOW, base_dir=tmp_path, fetcher=_fetcher(pages), knowledge_store=spy
    )  # seed — nothing fires, nothing written
    assert spy.items == []
    pages["v"] = FEED_THREE
    fire = web_poll.poll_one(
        trigger, now=NOW + 400, base_dir=tmp_path, fetcher=_fetcher(pages), knowledge_store=spy
    )
    assert fire.payload is not None
    assert len(spy.items) == 1, "only the ONE genuinely-new item is written"
    written = spy.items[0]
    assert written["item_type"] == "bookmark"
    assert written["provider"] == "web_watch"
    assert "post-3" in written["title"]


def test_only_GENUINELY_NEW_items_are_written_not_the_whole_page(store, tmp_path):
    """The seen-set gates 'new', so a re-poll of an unchanged page writes nothing more."""
    trigger = _watch(store)
    spy = _SpyKnowledge()
    pages = {"v": FEED_TWO}
    web_poll.poll_one(  # seed
        trigger, now=NOW, base_dir=tmp_path, fetcher=_fetcher(pages), knowledge_store=spy
    )
    pages["v"] = FEED_THREE
    web_poll.poll_one(  # fires: 1 new
        trigger, now=NOW + 400, base_dir=tmp_path, fetcher=_fetcher(pages), knowledge_store=spy
    )
    web_poll.poll_one(  # unchanged: writes nothing
        trigger, now=NOW + 800, base_dir=tmp_path, fetcher=_fetcher(pages), knowledge_store=spy
    )
    assert len(spy.items) == 1


def test_the_digest_does_NOT_touch_the_memory_subsystem(store, tmp_path):
    """🔴 The routing contract: web_watch output is KNOWLEDGE (searchable user items), not memory.
    A real KnowledgeStore gains the item; a MemoryStore over the same tmp home stays empty."""
    from personalclaw.knowledge.store import KnowledgeStore
    from personalclaw.memory import MemoryStore

    kstore = KnowledgeStore(str(tmp_path / "k.db"))
    memory = MemoryStore(workspace=tmp_path / "ws")
    memory.init()

    trigger = _watch(store)
    pages = {"v": FEED_TWO}
    web_poll.poll_one(
        trigger, now=NOW, base_dir=tmp_path, fetcher=_fetcher(pages), knowledge_store=kstore
    )
    pages["v"] = FEED_THREE
    web_poll.poll_one(
        trigger, now=NOW + 400, base_dir=tmp_path, fetcher=_fetcher(pages), knowledge_store=kstore
    )

    # KNOWLEDGE gained the item …
    assert kstore.search_items_fts_count("post-3") >= 1
    # … and MEMORY was never touched: no history/preferences/projects writes beyond init defaults.
    assert "post-3" not in memory.read_preferences()
    assert "post-3" not in memory.read_projects()
    hist = list((tmp_path / "ws" / "memory" / "history").glob("*.md"))
    assert hist == [], "web_watch must not write memory history"


def test_the_new_spec_keys_are_ACCEPTED_by_validation():
    """`escalate_headless` / `max_headless_requests` must be in `SPEC_KEYS['web_watch']`, or an
    opted-in watch validates with an 'unknown key' warning."""
    from personalclaw.triggers.models import validate_spec

    issues = validate_spec(
        "web_watch",
        {"url": "https://x.example", "escalate_headless": True, "max_headless_requests": 3},
    )
    assert issues == [], f"new headless keys must be recognised; got {issues}"
