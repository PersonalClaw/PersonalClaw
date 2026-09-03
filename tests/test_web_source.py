"""WATCHED-SOURCES WS-3 — web-source detectors, selector configs, escalating fetch, preview.

Every clause of the atom's ``done_when`` is asserted as a COUNT or a structural fact, never
as "the poll succeeded":

* **auto-detection on a real listing page** — a changelog yields exactly three items AND the
  test asserts WHICH detector won, because "three items appeared" is also what a lucky
  frequency match looks like;
* **a homepage yields nothing** — zero items AND the pick-a-listing-page guidance string, so
  a regression that returns an empty list with no remediation reds;
* **a manual selector config rescues a page auto-detection fails on** — the SAME page is
  asserted at 0 items on auto and N items with ``spec.extraction``;
* **the render escalation is budgeted and recorded** — the JS-heavy page succeeds only after
  the render tier, the REQUEST COUNT is asserted (so an unbudgeted retry storm reds), and the
  escalation is read back off the persisted poll record;
* **allow_render: false degrades visibly** — zero items, the ``needs render tier`` health
  status, and ``render_fn`` patched to RAISE, which is what proves the tier is never reached
  rather than merely unproductive;
* **each of the five detectors has its own case**, plus an adversarial case per §2.2 hygiene
  default (an off-domain link dropped, a two-word title rejected, HTML sanitized);
* **zero tokens in the detection path** — every model seam is patched to RAISE across a
  preview AND a poll, with a vacuity counterpart proving the patches are live.

No test opens a socket: ``fetch_fn``/``render_fn`` are the provider's only two byte seams and
every test injects a recorded response. Isolation: tmp_path db + PERSONALCLAW_HOME.
"""

import json

import pytest

from personalclaw.knowledge.source_engine import SourceEngine
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.knowledge_providers.base import HEALTH_NEEDS_RENDER, HEALTH_OK
from personalclaw.knowledge_providers.web_source import (
    DEFAULT_MIN_WORDS_TITLE,
    DETECTOR_JSON_LD,
    DETECTOR_JSON_STATE,
    DETECTOR_MANUAL,
    DETECTOR_ORDER,
    DETECTOR_SELECTOR_FREQUENCY,
    DETECTOR_SEMANTIC_HTML,
    DETECTOR_WORDPRESS_API,
    JS_SHELL_MAX_TEXT_CHARS,
    LISTING_PAGE_GUIDANCE,
    MAX_ITEMS_PER_POLL,
    SPEC_SCHEMA,
    WebSourceProvider,
    looks_like_js_shell,
)

PAGE_URL = "https://app.example.com/changelog"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


# ── recorded responses + seams ──────────────────────────────────────────────────────


class _Resp:
    """A recorded fetch response — the shape ``net.fetch`` returns."""

    def __init__(self, body="", *, status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self.text = body
        self.url = PAGE_URL


class _Fetcher:
    """A scripted fetch seam. Routes by URL SUBSTRING so a detector's sub-request (the
    WordPress REST call) is answered separately from the page, and records every request so a
    test can assert the budget was really respected rather than merely not exceeded."""

    def __init__(self, default=None, routes=None):
        self.default = default
        self.routes = dict(routes or {})
        self.requests: list[str] = []
        self.headers: list[dict] = []

    async def __call__(self, url, *, policy=None, headers=None):
        self.requests.append(url)
        self.headers.append(dict(headers or {}))
        for needle, resp in self.routes.items():
            if needle in url:
                return resp
        if self.default is None:
            raise AssertionError(f"unrouted fetch: {url}")
        return self.default


class _Render:
    """A scripted render seam standing in for ``web/render.py::render_url``."""

    def __init__(self, html="", *, ok=True, unavailable=False, error=""):
        self.html = html
        self.ok = ok
        self.unavailable = unavailable
        self.error = error
        self.calls: list[str] = []

    async def __call__(self, url, *, policy=None):
        self.calls.append(url)
        return self


class _Exploding:
    """A seam that must never be reached. Patched in where the test's whole point is that a
    tier is not merely unproductive but genuinely never called."""

    def __init__(self, what):
        self.what = what

    async def __call__(self, *a, **kw):
        raise AssertionError(f"{self.what} must never be reached")


class _FakeQueue:
    def __init__(self):
        self.enqueued: list[str] = []

    def enqueue(self, item_id: str) -> None:
        self.enqueued.append(item_id)

    def recover_pending(self) -> int:
        return 0


def _cfg(**over):
    from personalclaw.config.loader import SourcesConfig

    base = dict(
        enabled=True,
        poll_interval_default_secs=1,
        network_floor_secs=0,
        max_sources=100,
        max_items_per_poll=50,
        daily_request_budget=288,
    )
    base.update(over)
    return SourcesConfig(**base)


def _setup(store, fetcher, *, spec=None, budget=None, render=None, browse=None, url=PAGE_URL):
    sid = store.create_source(
        name="page",
        provider="watched-page",
        kind="web_page",
        spec=spec or {"url": url},
        budget=budget or {},
        item_type="bookmark",
    )
    provider = WebSourceProvider(store, fetch_fn=fetcher, render_fn=render, browse_fn=browse)
    queue = _FakeQueue()
    engine = SourceEngine(store, queue, providers_lister=lambda: [provider], config_loader=_cfg)
    return sid, provider, engine, queue


async def _poll(engine, store, sid):
    return await engine.poll_source(store.get_source(sid), _cfg())


def _items(store, sid):
    return store.db.execute(
        "SELECT * FROM items WHERE source_id = ? ORDER BY guid", (sid,)
    ).fetchall()


# ── page fixtures ───────────────────────────────────────────────────────────────────

_PROSE = (
    "Acme is the fastest way to ship your product to the people who need it, with a "
    "deployment pipeline that runs in seconds and a rollback that never loses a byte. "
    "Teams of every size use Acme to keep their releases boring, their dashboards quiet "
    "and their weekends free from incident pages that should never have happened. "
    "Start today and see the difference within an afternoon of real work."
)


def _changelog(*, link_host="", body_extra="") -> str:
    """Three ``<article>`` entries — the archetypal listing page (a changelog)."""
    host = link_host or ""
    entries = "".join(
        f'<article><h2><a href="{host}/changelog/v{v}">Version {v} released today</a></h2>'
        f'<time datetime="2026-08-0{i}">Aug {i}</time>'
        f"<p>Adds the streaming importer and fixes {i} crashes.</p></article>"
        for i, v in enumerate(("2.1.0", "2.0.9", "2.0.8"), start=1)
    )
    return (
        "<html><head><title>Changelog</title></head><body><main>"
        f"{entries}{body_extra}</main></body></html>"
    )


def _json_ld_listing() -> str:
    """A listing that ALSO carries ``<article>`` markup — so the test can prove ordering."""
    blob = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "item": {
                    "@type": "BlogPosting",
                    "headline": f"Structured post number {n}",
                    "url": f"https://app.example.com/p/{n}",
                    "description": f"Body of structured post {n}.",
                    "datePublished": f"2026-07-0{n}",
                },
            }
            for n in (1, 2)
        ],
    }
    return (
        '<html><body><script type="application/ld+json">'
        + json.dumps(blob)
        + "</script>"
        + _changelog()
        + "</body></html>"
    )


def _wordpress_page() -> str:
    """A WP page that ALSO has articles, so the budget test can watch the stack fall through
    from ``wordpress_api`` to ``semantic_html`` when the sub-request is refused."""
    return (
        '<html><head><link rel="https://api.w.org/" href="https://app.example.com/wp-json/">'
        "</head><body>" + _changelog() + "</body></html>"
    )


_WP_POSTS = json.dumps(
    [
        {
            "id": 41,
            "link": "https://app.example.com/2026/07/wp-one",
            "title": {"rendered": "WordPress post number one"},
            "excerpt": {"rendered": "<p>An excerpt for post one.</p>"},
            "date_gmt": "2026-07-01T10:00:00",
        },
        {
            "id": 42,
            "link": "https://app.example.com/2026/07/wp-two",
            "title": {"rendered": "WordPress post number two"},
            "excerpt": {"rendered": "<p>An excerpt for post two.</p>"},
            "date_gmt": "2026-07-02T10:00:00",
        },
    ]
)


def _next_data_page() -> str:
    """An SPA that ships its items inside its own state blob — no markup to scrape."""
    blob = {
        "props": {
            "pageProps": {
                "posts": [
                    {
                        "title": f"State post number {n}",
                        "slug": f"/blog/state-{n}",
                        "excerpt": f"Excerpt of state post {n}.",
                        "date": f"2026-06-0{n}",
                        "id": f"sp-{n}",
                    }
                    for n in (1, 2, 3)
                ]
            }
        }
    }
    return (
        '<html><body><div id="__next"></div>'
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(blob)
        + "</script></body></html>"
    )


def _card_grid() -> str:
    """No article, no JSON, no semantics — only a repeated class signature."""
    cards = "".join(
        f'<div class="card"><h3><a href="/posts/{slug}">{name} release notes published</a>'
        f"</h3><p>Body text for {slug}.</p></div>"
        for slug, name in (("alpha", "Alpha"), ("beta", "Beta"), ("gamma", "Gamma"))
    )
    return f'<html><body><div class="wrap">{cards}</div></body></html>'


def _homepage() -> str:
    """A marketing homepage: navigation, a hero, feature sections, a footer, and a script —
    everything a naive detector mistakes for items, and prose well past the JS-shell floor."""
    return (
        "<html><head><title>Acme</title></head><body>"
        '<script src="/analytics.js"></script>'
        '<nav><a href="/">Home</a><a href="/pricing">Pricing</a><a href="/docs">Docs</a>'
        '<a href="/blog">Blog</a></nav>'
        f"<header><h1>Ship faster with Acme</h1><p>{_PROSE}</p>"
        '<a href="/signup">Get started</a></header>'
        '<section class="feature"><h2>Fast</h2><p>Very fast indeed, and measurably so.</p>'
        "</section>"
        '<section class="feature"><h2>Safe</h2><p>Very safe indeed, and provably so.</p>'
        "</section>"
        '<footer><a href="/privacy">Privacy</a><a href="/terms">Terms</a></footer>'
        "</body></html>"
    )


def _linkless_table() -> str:
    """A JS-lite page every detector legitimately misses: real content, no links, no
    semantics, no JSON. The §2.2 escape hatch exists for exactly this page."""
    rows = "".join(
        f'<span class="row"><b class="t">Release {v} ships the queue rewrite</b>'
        f'<em class="d">2026-06-0{i}</em>'
        f'<i class="x">Notes about the {v} queue rewrite in detail.</i></span>'
        for i, v in enumerate(("4.0", "4.1", "4.2"), start=1)
    )
    return f'<html><body><div class="log">{rows}</div><p>{_PROSE}</p></body></html>'


def _js_shell() -> str:
    return (
        '<html><head><title>App</title></head><body><div id="root"></div>'
        '<script src="/bundle.js"></script>'
        "<noscript>You need JavaScript to run this app.</noscript></body></html>"
    )


# ── SC#1: auto-detection on a real listing page, and WHICH detector won ─────────────


@pytest.mark.asyncio
async def test_a_changelog_yields_three_items_via_semantic_html(store):
    fetcher = _Fetcher(_Resp(_changelog()))
    sid, _p, engine, queue = _setup(store, fetcher)

    assert await _poll(engine, store, sid) == 3
    rows = _items(store, sid)
    assert len(rows) == 3, [dict(r) for r in rows]
    titles = sorted(r["title"] for r in rows)
    assert titles == [
        "Version 2.0.8 released today",
        "Version 2.0.9 released today",
        "Version 2.1.0 released today",
    ]
    # Relative hrefs resolved against the page, so identity is a real URL.
    assert all(r["url"].startswith("https://app.example.com/changelog/v") for r in rows)
    assert len(queue.enqueued) == 3
    assert store.get_source(sid)["health_status"] == HEALTH_OK
    # WHICH detector won is part of the clause: three items is also what a lucky frequency
    # match looks like, and the user tunes a NAMED detector.
    provider = WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(_changelog())))
    preview = await provider.preview({"url": PAGE_URL})
    assert preview.detector == DETECTOR_SEMANTIC_HTML
    assert [i.metadata["detector"] for i in preview.items] == [DETECTOR_SEMANTIC_HTML] * 3


@pytest.mark.asyncio
async def test_json_ld_wins_over_semantic_html_on_a_page_carrying_both(store):
    """Reliability order, asserted where it is falsifiable: the page declares an ItemList AND
    ships article markup, so whichever detector is tried first decides the answer."""
    provider = WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(_json_ld_listing())))
    preview = await provider.preview({"url": PAGE_URL})
    assert preview.detector == DETECTOR_JSON_LD
    assert [i.title for i in preview.items] == [
        "Structured post number 1",
        "Structured post number 2",
    ]


@pytest.mark.asyncio
async def test_a_spec_detector_list_filters_the_stack_but_cannot_reorder_it(store):
    """A spec narrows the stack; it never promotes a heuristic above a declaration."""
    body = _json_ld_listing()
    prov = WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(body)))
    narrowed = await prov.preview({"url": PAGE_URL, "detectors": [DETECTOR_SEMANTIC_HTML]})
    assert narrowed.detector == DETECTOR_SEMANTIC_HTML
    assert len(narrowed.items) == 3

    prov2 = WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(body)))
    both = await prov2.preview(
        {"url": PAGE_URL, "detectors": [DETECTOR_SEMANTIC_HTML, DETECTOR_JSON_LD]}
    )
    assert both.detector == DETECTOR_JSON_LD, "order is the provider's, not the config's"


@pytest.mark.asyncio
async def test_wordpress_api_detector_pulls_structured_posts(store):
    fetcher = _Fetcher(routes={"wp-json": _Resp(_WP_POSTS), "changelog": _Resp(_wordpress_page())})
    provider = WebSourceProvider(store, fetch_fn=fetcher)
    preview = await provider.preview({"url": PAGE_URL})
    assert preview.detector == DETECTOR_WORDPRESS_API
    assert [i.title for i in preview.items] == [
        "WordPress post number one",
        "WordPress post number two",
    ]
    assert preview.requests_used == 2, "the page plus the one REST call"
    assert len(fetcher.requests) == 2
    assert fetcher.requests[1].endswith("/wp-json/wp/v2/posts?per_page=20")


@pytest.mark.asyncio
async def test_a_wordpress_title_is_decoded_not_left_html_escaped(store):
    """WP's REST API HTML-ESCAPES `title.rendered`, and a title is plain text on every
    surface. Found by driving WS-9's create flow against a real WordPress changelog: all 20
    previewed rows read `Don&#8217;t stop early` rather than the apostrophe. The excerpt is
    deliberately NOT decoded — it is markup whose escaping is meaningful — so the same
    fixture asserts both halves."""
    posts = json.dumps(
        [
            {
                "id": 7,
                "link": "https://app.example.com/2026/07/dont-stop",
                "title": {"rendered": "Don&#8217;t stop early: case-folding at speed"},
                "excerpt": {"rendered": "<p>An escaped &lt;script&gt; tag, shown as code.</p>"},
                "date_gmt": "2026-07-03T10:00:00",
            }
        ]
    )
    fetcher = _Fetcher(routes={"wp-json": _Resp(posts), "changelog": _Resp(_wordpress_page())})
    provider = WebSourceProvider(store, fetch_fn=fetcher)

    preview = await provider.preview({"url": PAGE_URL})

    assert preview.detector == DETECTOR_WORDPRESS_API
    assert preview.items[0].title == "Don\u2019t stop early: case-folding at speed"
    # The excerpt keeps its escaping: decoding it would turn shown code back into live markup
    # before `sanitize_html` ever saw it.
    assert "&lt;script&gt;" in preview.items[0].content


@pytest.mark.asyncio
async def test_json_state_detector_reads_an_spa_state_blob(store):
    provider = WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(_next_data_page())))
    preview = await provider.preview({"url": PAGE_URL})
    assert preview.detector == DETECTOR_JSON_STATE
    assert len(preview.items) == 3
    assert preview.items[0].title == "State post number 1"
    # The slug was resolved against the page, so the item has a usable link + identity.
    assert preview.items[0].url == "https://app.example.com/blog/state-1"
    # A state blob that yields items must NOT trigger a render escalation even though the
    # page is markup-empty: the outcome was items, and outcome is what §2.3 escalates on.
    assert preview.escalations == []
    assert preview.requests_used == 1


@pytest.mark.asyncio
async def test_a_declared_state_blob_outranks_a_frequent_selector(store):
    """The DEVIATION from §2.1's table order, asserted as an outcome rather than a sequence.

    `selector_frequency` was moved behind `json_state` because it is the only detector that
    infers structure the page never declared. `DETECTOR_ORDER` alone cannot prove that: a
    schema-enum parity test reds on any reorder, including a harmless one, while a page that
    satisfies BOTH detectors is the only thing that shows which one actually wins. This page
    carries a real `__NEXT_DATA__` blob AND a thrice-repeated card signature; the declaration
    must win, and the items must be the state's, not the markup's.
    """
    both = _next_data_page().replace("</body>", _card_grid().split("<body>")[1])
    provider = WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(both)))
    preview = await provider.preview({"url": PAGE_URL})
    assert preview.detector == DETECTOR_JSON_STATE, "a heuristic must not outrank a declaration"
    assert [i.title for i in preview.items] == [
        "State post number 1",
        "State post number 2",
        "State post number 3",
    ], "the state blob's items, not the card grid's"


@pytest.mark.asyncio
async def test_selector_frequency_detector_wins_on_a_bare_card_grid(store):
    provider = WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(_card_grid())))
    preview = await provider.preview({"url": PAGE_URL})
    assert preview.detector == DETECTOR_SELECTOR_FREQUENCY
    assert [i.title for i in preview.items] == [
        "Alpha release notes published",
        "Beta release notes published",
        "Gamma release notes published",
    ], "the repeated card signature won, not the equally-frequent inner heading"


# ── SC#1: a homepage yields the pick-a-listing-page guidance ────────────────────────


@pytest.mark.asyncio
async def test_a_homepage_yields_zero_items_and_the_listing_page_guidance(store):
    """Zero items is never a bare empty result. The guidance STRING is asserted, because an
    empty list with no remediation is the failure §2.1's diagnosis UX exists to prevent."""
    fetcher = _Fetcher(_Resp(_homepage()))
    sid, provider, engine, queue = _setup(store, fetcher)

    preview = await provider.preview({"url": PAGE_URL})
    assert preview.items == []
    assert preview.detector == ""
    assert "LISTING pages" in preview.guidance
    assert preview.guidance == LISTING_PAGE_GUIDANCE
    assert preview.health_status == "", "a wrong URL is not a render-tier problem"

    assert await _poll(engine, store, sid) == 0
    assert _items(store, sid) == []
    assert queue.enqueued == []
    row = store.get_source(sid)
    assert "LISTING pages" in (row["last_error_summary"] or "")
    assert row["health_status"] == "degraded"


def test_a_homepage_with_scripts_is_not_classed_as_a_js_shell():
    """The discrimination is MEASURED text volume, not the presence of script. Without this
    the homepage and the JS shell would both be reported as 'needs render tier' and the user
    would be sent to a browser to re-read the same nothing."""
    from personalclaw.knowledge_providers.html_dom import parse_html

    home = _homepage()
    shell = _js_shell()
    assert not looks_like_js_shell(home, parse_html(home))
    assert looks_like_js_shell(shell, parse_html(shell))
    # And the floor is really the discriminator, not an accident of these two fixtures.
    from personalclaw.knowledge_providers.html_dom import parse_html as _p

    body = _p(home)
    visible = next(n for n in body.iter_descendants() if n.tag == "body")
    assert len(visible.text) > JS_SHELL_MAX_TEXT_CHARS
    shell_body = next(n for n in _p(shell).iter_descendants() if n.tag == "body")
    assert len(shell_body.text) < JS_SHELL_MAX_TEXT_CHARS


# ── SC#1: a manual selector config rescues a JS-lite failure ────────────────────────


_MANUAL_EXTRACTION = {
    "items": {"selector": "span.row"},
    "title": {"selector": "b.t", "extractor": "text"},
    "published_at": {
        "selector": "em.d",
        "extractor": "text",
        "post_process": [{"name": "parse_time"}],
    },
    "description": {"selector": "i.x", "extractor": "text"},
}


@pytest.mark.asyncio
async def test_a_manual_selector_config_rescues_a_page_auto_detection_misses(store):
    """Both halves on the SAME page: auto → 0, manual → 3. Asserting only the manual half
    would pass on a page auto-detection handles fine, proving nothing about the escape hatch.
    """
    page = _linkless_table()

    auto = await WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(page))).preview({"url": PAGE_URL})
    assert auto.items == [], "no links, no semantics, no JSON — every detector must miss"
    assert auto.guidance == LISTING_PAGE_GUIDANCE

    fetcher = _Fetcher(_Resp(page))
    sid, _p, engine, queue = _setup(
        store, fetcher, spec={"url": PAGE_URL, "extraction": _MANUAL_EXTRACTION}
    )
    assert await _poll(engine, store, sid) == 3
    rows = _items(store, sid)
    assert len(rows) == 3
    assert sorted(r["title"] for r in rows) == [
        "Release 4.0 ships the queue rewrite",
        "Release 4.1 ships the queue rewrite",
        "Release 4.2 ships the queue rewrite",
    ]
    assert len(queue.enqueued) == 3
    manual = await WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(page))).preview(
        {"url": PAGE_URL, "extraction": _MANUAL_EXTRACTION}
    )
    assert manual.detector == DETECTOR_MANUAL


@pytest.mark.asyncio
async def test_a_manual_config_replaces_the_stack_rather_than_falling_back_to_it(store):
    """A broken selector must FAIL VISIBLY on a page the detectors could have handled —
    silently falling back would hide the user's real mistake behind a lucky auto match."""
    prov = WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(_changelog())))
    got = await prov.preview({"url": PAGE_URL, "extraction": {"items": {"selector": "div.nope"}}})
    assert got.items == []
    assert got.detector == DETECTOR_MANUAL, "not semantic_html — the config is authoritative"


@pytest.mark.asyncio
async def test_post_process_chain_runs_in_order_with_the_declared_extractors(store):
    page = (
        '<html><body><div class="e">'
        '<span class="h">  Prefix: A real headline here  </span>'
        '<a class="l" href="/deep/one">go</a>'
        '<div class="b"><p>Body <b>with</b> markup</p><script>alert(1)</script></div>'
        '<span class="dt">Mon, 01 Jun 2026 00:00:00 GMT</span>'
        "</div></body></html>"
    )
    prov = WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(page)))
    got = await prov.preview(
        {
            "url": PAGE_URL,
            "extraction": {
                "items": {"selector": "div.e"},
                "title": {
                    "selector": "span.h",
                    "extractor": "text",
                    "post_process": [
                        {"name": "gsub", "pattern": "^Prefix: ", "replacement": ""},
                        {"name": "template", "string": "[{value}]"},
                    ],
                },
                "url": {"selector": "a.l", "extractor": "href"},
                "description": {
                    "selector": "div.b",
                    "extractor": "html",
                    "post_process": [{"name": "html_to_markdown"}],
                },
                "published_at": {
                    "selector": "span.dt",
                    "extractor": "text",
                    "post_process": [{"name": "parse_time"}],
                },
            },
        }
    )
    assert len(got.items) == 1
    item = got.items[0]
    assert item.title == "[A real headline here]"
    assert item.url == "https://app.example.com/deep/one", "href resolved without asking"
    assert item.published_at.startswith("2026-06-01T"), item.published_at
    assert "with" in item.content and "markup" in item.content
    # §2.2's sanitize_html default-ON, prepended ahead of the config's own chain.
    assert "alert(1)" not in item.content
    assert "<script" not in item.content


_SCRIPTY_PAGE = (
    '<html><body><div class="e"><h3>A perfectly good headline</h3>'
    '<div class="b"><p>Body copy.</p><script>alert(1)</script>'
    "<img src=x onerror=alert(2)></div></div></body></html>"
)
_HTML_EXTRACTION = {
    "items": {"selector": "div.e"},
    "title": {"selector": "h3", "extractor": "text"},
    "description": {"selector": "div.b", "extractor": "html"},
}


@pytest.mark.asyncio
async def test_sanitize_html_is_on_by_default_for_the_html_extractor(store):
    """Isolated from the chain test on purpose: there, ``html_to_markdown`` also removes the
    script, so that test would still pass with the default turned off. Here the extracted
    value stays HTML, so the default is the only thing standing between a page's script and
    the stored item."""
    on = await WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(_SCRIPTY_PAGE))).preview(
        {"url": PAGE_URL, "extraction": _HTML_EXTRACTION}
    )
    assert len(on.items) == 1
    assert "Body copy." in on.items[0].content
    assert "alert(1)" not in on.items[0].content
    assert "onerror" not in on.items[0].content

    off = await WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(_SCRIPTY_PAGE))).preview(
        {"url": PAGE_URL, "extraction": _HTML_EXTRACTION, "sanitize_html": False}
    )
    # The vacuity counterpart: opting out is a VISIBLE decision, and it proves the sanitizer
    # is what removed the script above rather than the extractor never having seen it.
    assert "alert(1)" in off.items[0].content


@pytest.mark.asyncio
async def test_the_static_and_attribute_extractors_are_wired(store):
    page = (
        '<html><body><li class="r" data-key="k-9">'
        "<h4>A perfectly fine title</h4></li></body></html>"
    )
    prov = WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(page)))
    got = await prov.preview(
        {
            "url": PAGE_URL,
            "extraction": {
                "items": {"selector": "li.r"},
                "title": {"selector": "h4", "extractor": "text"},
                "guid": {"extractor": "attribute", "attribute": "data-key"},
                "author": {"extractor": "static", "value": "The Editors"},
            },
        }
    )
    assert [i.guid for i in got.items] == ["k-9"]
    assert got.items[0].metadata["author"] == "The Editors"


# ── SC#2: escalation is outcome-driven, budgeted, and recorded ──────────────────────


@pytest.mark.asyncio
async def test_a_js_heavy_page_succeeds_only_after_the_render_tier_and_records_it(store):
    fetcher = _Fetcher(_Resp(_js_shell()))
    render = _Render(_changelog())
    sid, _p, engine, queue = _setup(
        store,
        fetcher,
        budget={"max_requests": 5, "allow_render": True},
        render=render,
    )

    assert await _poll(engine, store, sid) == 3
    assert len(_items(store, sid)) == 3
    assert len(queue.enqueued) == 3
    # ONE budget across both tiers: exactly one plain fetch and one render, no retry storm.
    assert len(fetcher.requests) == 1
    assert len(render.calls) == 1
    row = store.get_source(sid)
    assert row["health_status"] == HEALTH_OK
    assert row["last_escalations"] == ["escalated to render tier; extracted 3 item(s)"]


@pytest.mark.asyncio
async def test_tier_one_alone_extracts_nothing_from_the_same_js_heavy_page(store):
    """The vacuity counterpart to the escalation test: without it, a render tier that was
    never actually needed would make the test above pass while proving nothing."""
    prov = WebSourceProvider(
        store, fetch_fn=_Fetcher(_Resp(_js_shell())), render_fn=_Render("", ok=False)
    )
    got = await prov.preview({"url": PAGE_URL}, budget={"allow_render": False})
    assert got.items == []


@pytest.mark.asyncio
async def test_allow_render_false_degrades_to_needs_render_tier_and_never_renders(store):
    """``render_fn`` RAISES, so the tier is proven unreachable rather than merely unhelpful."""
    fetcher = _Fetcher(_Resp(_js_shell()))
    sid, _p, engine, queue = _setup(
        store,
        fetcher,
        budget={"max_requests": 5},  # allow_render omitted → default false (§2.3)
        render=_Exploding("the render tier"),
    )

    assert await _poll(engine, store, sid) == 0
    assert _items(store, sid) == []
    assert queue.enqueued == []
    row = store.get_source(sid)
    assert row["health_status"] == HEALTH_NEEDS_RENDER
    assert row["health_status"] == "needs render tier"
    assert row["last_escalations"] == ["render tier needed but budget.allow_render is false"]
    assert "allow_render" in (row["last_error_summary"] or "")
    assert len(fetcher.requests) == 1, "the refusal costs one request, not a retry"


@pytest.mark.asyncio
async def test_a_truthy_non_true_allow_render_does_not_license_a_browser(store):
    """A hand-edited row carrying the string "false" must not read as permission."""
    sid, _p, engine, _q = _setup(
        store,
        _Fetcher(_Resp(_js_shell())),
        budget={"allow_render": "false"},
        render=_Exploding("the render tier"),
    )
    assert await _poll(engine, store, sid) == 0
    assert store.get_source(sid)["health_status"] == HEALTH_NEEDS_RENDER


@pytest.mark.asyncio
async def test_the_render_escalation_is_refused_when_the_poll_budget_is_spent(store):
    """One shared budget: a max_requests of 1 is spent by the plain fetch, so the render is
    refused VISIBLY rather than being made off-budget."""
    fetcher = _Fetcher(_Resp(_js_shell()))
    sid, _p, engine, _q = _setup(
        store,
        fetcher,
        budget={"max_requests": 1, "allow_render": True},
        render=_Exploding("the render tier"),
    )
    assert await _poll(engine, store, sid) == 0
    row = store.get_source(sid)
    assert len(fetcher.requests) == 1
    assert row["last_escalations"] == [
        "render escalation refused: per-poll request budget spent (1 requests)"
    ]


@pytest.mark.asyncio
async def test_a_wordpress_sub_request_draws_on_the_same_poll_budget(store):
    """§2.3's "all attempts in one poll draw on a single max_requests", made falsifiable: with
    a budget of 1 the REST call cannot happen, so the stack falls through to semantic_html."""
    fetcher = _Fetcher(routes={"wp-json": _Resp(_WP_POSTS), "changelog": _Resp(_wordpress_page())})
    prov = WebSourceProvider(store, fetch_fn=fetcher)
    got = await prov.preview({"url": PAGE_URL}, budget={"max_requests": 1})
    assert len(fetcher.requests) == 1, "the sub-request had no budget to spend"
    assert got.detector == DETECTOR_SEMANTIC_HTML
    assert len(got.items) == 3
    assert got.requests_used == 1


@pytest.mark.asyncio
async def test_an_unavailable_render_tier_is_still_needs_render_tier(store):
    sid, _p, engine, _q = _setup(
        store,
        _Fetcher(_Resp(_js_shell())),
        budget={"allow_render": True},
        render=_Render("", ok=False, unavailable=True, error="Playwright is not installed."),
    )
    assert await _poll(engine, store, sid) == 0
    row = store.get_source(sid)
    assert row["health_status"] == HEALTH_NEEDS_RENDER
    assert row["last_escalations"] == ["render tier unavailable; install personalclaw[js-render]"]


@pytest.mark.asyncio
async def test_a_render_that_still_finds_nothing_records_the_attempt_and_the_guidance(store):
    sid, _p, engine, _q = _setup(
        store,
        _Fetcher(_Resp(_js_shell())),
        budget={"allow_render": True},
        render=_Render(_homepage()),
    )
    assert await _poll(engine, store, sid) == 0
    row = store.get_source(sid)
    assert row["last_escalations"] == ["escalated to render tier; still no items after JS render"]
    assert "LISTING pages" in (row["last_error_summary"] or "")


@pytest.mark.asyncio
async def test_escalations_are_overwritten_per_poll_not_appended(store):
    """The rollup describes the LAST poll's cost. An appending list on a row a UI reads is a
    log in a rollup column, and would make one bad afternoon permanent."""
    fetcher = _Fetcher(_Resp(_js_shell()))
    sid, _p, engine, _q = _setup(
        store, fetcher, budget={"allow_render": True}, render=_Render(_changelog())
    )
    await _poll(engine, store, sid)
    await _poll(engine, store, sid)
    assert len(store.get_source(sid)["last_escalations"]) == 1


# ── §(d) tier 3: the gateway browse escalation (BA-6) ───────────────────────────────


@pytest.mark.asyncio
async def test_browse_tier_extracts_when_the_render_is_still_a_shell(store):
    # Plain fetch is a shell, the render tier ALSO returns a shell, so the poll escalates to one
    # gateway browse tick — which finally renders the entries.
    browse = _Render(_changelog())
    sid, _p, engine, _q = _setup(
        store,
        _Fetcher(_Resp(_js_shell())),
        budget={"allow_render": True, "allow_browse": True},
        render=_Render(_js_shell()),
        browse=browse,
    )
    assert await _poll(engine, store, sid) == 3
    row = store.get_source(sid)
    assert row["last_escalations"] == [
        "escalated to render tier; still no items after JS render",
        "escalated to browse tier; extracted 3 item(s)",
    ]
    assert browse.calls == [PAGE_URL]


@pytest.mark.asyncio
async def test_browse_tier_needed_but_not_allowed_is_a_distinct_status(store):
    # Render tier exhausted on a still-shell page with browse not licensed: a distinct, actionable
    # status, and the browser is never launched.
    browse = _Render(_changelog())
    sid, _p, engine, _q = _setup(
        store,
        _Fetcher(_Resp(_js_shell())),
        budget={"allow_render": True},
        render=_Render(_js_shell()),
        browse=browse,
    )
    assert await _poll(engine, store, sid) == 0
    row = store.get_source(sid)
    assert row["last_escalations"] == [
        "escalated to render tier; still no items after JS render",
        "browse tier needed but budget.allow_browse is false",
    ]
    assert "browse tier" in (row["last_error_summary"] or "")
    assert browse.calls == []  # not licensed → the browser is never launched


@pytest.mark.asyncio
async def test_browse_tier_that_still_finds_nothing_records_both_attempts(store):
    # The browse tick ran but the page is genuinely empty: both escalation notes are recorded.
    sid, _p, engine, _q = _setup(
        store,
        _Fetcher(_Resp(_js_shell())),
        budget={"allow_render": True, "allow_browse": True},
        render=_Render(_js_shell()),
        browse=_Render(_js_shell()),
    )
    assert await _poll(engine, store, sid) == 0
    assert store.get_source(sid)["last_escalations"] == [
        "escalated to render tier; still no items after JS render",
        "escalated to browse tier; still no items after browse render",
    ]


@pytest.mark.asyncio
async def test_browse_tier_soft_fails_when_no_gateway_is_configured(store):
    # No injected seam: the REAL gateway-backed tick runs (the poll's `execute_tick` → the content
    # runner → the gateway opener). With no budget.cdp_url it fails soft (empty render, no browser
    # launched), proving the production wiring is connected end to end without a live browser.
    sid, _p, engine, _q = _setup(
        store,
        _Fetcher(_Resp(_js_shell())),
        budget={"allow_render": True, "allow_browse": True},
        render=_Render(_js_shell()),
    )
    assert await _poll(engine, store, sid) == 0
    assert store.get_source(sid)["last_escalations"] == [
        "escalated to render tier; still no items after JS render",
        "escalated to browse tier; still no items after browse render",
    ]


# ── §2.2 output hygiene: one adversarial case per default ───────────────────────────


@pytest.mark.asyncio
async def test_an_off_domain_link_is_dropped_by_default_and_kept_on_request(store):
    page = (
        "<html><body><main>"
        '<article><h2><a href="https://app.example.com/p/mine">A post on my own site</a></h2>'
        "<p>Body of my own post.</p></article>"
        '<article><h2><a href="https://ads.elsewhere.example/promo">A sponsored placement '
        "here</a></h2><p>Body of the ad.</p></article>"
        "</main></body></html>"
    )
    dropped = await WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(page))).preview(
        {"url": PAGE_URL}
    )
    assert [i.url for i in dropped.items] == ["https://app.example.com/p/mine"]

    kept = await WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(page))).preview(
        {"url": PAGE_URL, "keep_different_domain": "keep"}
    )
    assert len(kept.items) == 2


@pytest.mark.asyncio
async def test_a_subdomain_of_the_same_site_is_not_off_domain(store):
    page = (
        "<html><body><main>"
        '<article><h2><a href="https://blog.example.com/p/x">A post on the blog subdomain'
        "</a></h2><p>Body.</p></article>"
        '<article><h2><a href="https://app.example.com/p/y">A post on the app host</a></h2>'
        "<p>Body.</p></article>"
        "</main></body></html>"
    )
    got = await WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(page))).preview({"url": PAGE_URL})
    assert len(got.items) == 2


@pytest.mark.asyncio
async def test_a_two_word_title_is_rejected_and_a_bare_nav_link_is_dropped(store):
    page = (
        "<html><body><main>"
        '<article><h2><a href="/p/keep">A properly worded headline</a></h2>'
        "<p>Real body text.</p></article>"
        '<article><h2><a href="/p/short">Read more</a></h2>'
        "<p>Still has a real body paragraph.</p></article>"
        '<article><h2><a href="/p/nothing">Read more</a></h2></article>'
        "</main></body></html>"
    )
    got = await WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(page))).preview({"url": PAGE_URL})
    # Three articles in, two items out: the sub-three-word title is discarded, so the one
    # with a body survives titled by its URL and the one with NEITHER is dropped entirely.
    assert len(got.items) == 2
    assert got.items[0].title == "A properly worded headline"
    assert got.items[1].title == "https://app.example.com/p/short"
    assert DEFAULT_MIN_WORDS_TITLE == 3

    loose = await WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(page))).preview(
        {"url": PAGE_URL, "min_words_title": 1}
    )
    assert [i.title for i in loose.items] == [
        "A properly worded headline",
        "Read more",
        "Read more",
    ], "the floor is a knob, and lowering it is what proves it was the filter"


@pytest.mark.asyncio
async def test_an_item_with_no_derivable_identity_is_dropped(store):
    """Following feed_source: the seen-set can only gate what it can name, so an un-keyable
    item would re-ingest on every poll forever.

    The fixture has REAL BODY TEXT and nothing else — no link, no title, no date. That matters:
    a blank-everything row is already dropped by the title-or-description floor, so it would
    pass this test with the identity guard removed. Only a row that clears every OTHER floor
    isolates the guard being asserted here.
    """
    page = (
        '<html><body><ul><li class="r"><h4>   </h4>'
        "<p>Some real body text with no identity at all.</p></li></ul></body></html>"
    )
    extraction = {
        "items": {"selector": "li.r"},
        "title": {"selector": "h4", "extractor": "text"},
        "description": {"selector": "p", "extractor": "text"},
    }
    got = await WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(page))).preview(
        {"url": PAGE_URL, "extraction": extraction}
    )
    assert got.items == []

    # Vacuity floor: the same row WITH a date is keyable (title+published_at hash), so the
    # drop above is the identity guard rather than the extraction simply not working.
    keyable = page.replace("<h4>   </h4>", '<h4>   </h4><time datetime="2026-06-01">x</time>')
    with_date = await WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(keyable))).preview(
        {
            "url": PAGE_URL,
            "extraction": {
                **extraction,
                "published_at": {
                    "selector": "time",
                    "extractor": "attribute",
                    "attribute": "datetime",
                },
            },
        }
    )
    assert len(with_date.items) == 1
    assert with_date.items[0].guid


@pytest.mark.asyncio
async def test_hygiene_runs_per_detector_so_the_stack_falls_through(store):
    """A detector whose every candidate fails the §2.2 floors found NOTHING.

    The page gives ``semantic_html`` two ``<article>`` blocks that are pure off-domain
    sponsored links, and gives ``selector_frequency`` three real cards further down. Deciding
    the winner on RAW candidates would let the ads win and return zero items — which is
    exactly how a page's promo rail becomes "the source found nothing today".
    """
    page = (
        "<html><body><main>"
        '<article><h2><a href="https://ads.elsewhere.example/a">Sponsored placement one '
        "here</a></h2></article>"
        '<article><h2><a href="https://ads.elsewhere.example/b">Sponsored placement two '
        "here</a></h2></article>"
        "</main>"
        + _card_grid().replace("<html><body>", "").replace("</body></html>", "")
        + "</body></html>"
    )
    got = await WebSourceProvider(store, fetch_fn=_Fetcher(_Resp(page))).preview({"url": PAGE_URL})
    assert got.detector == DETECTOR_SELECTOR_FREQUENCY, "the stack must not stop at the ads"
    assert [i.title for i in got.items] == [
        "Alpha release notes published",
        "Beta release notes published",
        "Gamma release notes published",
    ]
    # And the earlier detector really did produce raw candidates, so this measures fall-through
    # rather than a detector that simply never matched.
    from personalclaw.knowledge_providers.html_dom import parse_html
    from personalclaw.knowledge_providers.web_source import detect_semantic_html

    assert len(detect_semantic_html(parse_html(page))) == 2


# ── SC#2/§2.4: preview is a dry run that still spends budget ────────────────────────


@pytest.mark.asyncio
async def test_preview_persists_nothing_but_still_spends_the_budget(store):
    fetcher = _Fetcher(_Resp(_changelog()))
    sid, provider, _e, queue = _setup(store, fetcher)
    before = store.db.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]

    got = await provider.preview({"url": PAGE_URL}, budget={"max_requests": 3})

    assert len(got.items) == 3, "a dry run still extracts"
    assert got.requests_used == 1, "and still costs a real request at a third party"
    assert store.db.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"] == before
    assert store.db.execute("SELECT COUNT(*) AS n FROM source_seen").fetchone()["n"] == 0
    assert store.get_source_cursor(sid) == ""
    assert store.get_source(sid)["last_poll_at"] is None
    assert queue.enqueued == []


@pytest.mark.asyncio
async def test_preview_refuses_an_invalid_spec_without_fetching(store):
    fetcher = _Fetcher()  # unrouted: any fetch raises
    got = await WebSourceProvider(store, fetch_fn=fetcher).preview({"url": "file:///etc/passwd"})
    assert got.error and got.items == []
    assert fetcher.requests == []


@pytest.mark.asyncio
async def test_a_zero_request_budget_refuses_before_the_first_fetch(store):
    fetcher = _Fetcher()
    got = await WebSourceProvider(store, fetch_fn=fetcher).preview(
        {"url": PAGE_URL}, budget={"max_requests": 0}
    )
    assert fetcher.requests == [], "an explicit 0 is a real budget, not an unset one"
    assert "budget spent" in got.error


# ── conditional GET (the cheap steady state) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_conditional_get_validators_round_trip_through_the_cursor(store):
    fetcher = _Fetcher(
        _Resp(
            _changelog(), headers={"ETag": '"v1"', "Last-Modified": "Mon, 01 Jun 2026 00:00:00 GMT"}
        )
    )
    sid, _p, engine, _q = _setup(store, fetcher)
    await _poll(engine, store, sid)
    assert json.loads(store.get_source_cursor(sid))["etag"] == '"v1"'
    assert fetcher.headers[0].get("If-None-Match") is None

    await _poll(engine, store, sid)
    assert fetcher.headers[1]["If-None-Match"] == '"v1"'
    assert fetcher.headers[1]["If-Modified-Since"] == "Mon, 01 Jun 2026 00:00:00 GMT"


@pytest.mark.asyncio
async def test_a_304_is_a_healthy_zero_item_poll_not_a_detection_failure(store):
    """A 304 must NOT collect the listing-page guidance: telling a working source to change
    its URL every quarter hour is exactly the wrong remediation."""
    fetcher = _Fetcher(_Resp(_changelog(), headers={"ETag": '"v1"'}))
    sid, _p, engine, _q = _setup(store, fetcher)
    await _poll(engine, store, sid)
    fetcher.default = _Resp("", status=304)

    assert await _poll(engine, store, sid) == 0
    row = store.get_source(sid)
    assert row["health_status"] == HEALTH_OK
    assert (row["last_error_summary"] or "") == ""
    assert json.loads(store.get_source_cursor(sid))["etag"] == '"v1"', "validators kept"


@pytest.mark.asyncio
async def test_polling_the_same_page_twice_produces_no_duplicate_items(store):
    fetcher = _Fetcher(_Resp(_changelog()))
    sid, _p, engine, queue = _setup(store, fetcher)
    assert await _poll(engine, store, sid) == 3
    assert await _poll(engine, store, sid) == 0
    assert len(_items(store, sid)) == 3
    assert len(queue.enqueued) == 3


# ── the schema IS the validator (§2.2 single source of truth) ────────────────────────


def _schema_types(node, acc):
    if isinstance(node, dict):
        if "type" in node and isinstance(node["type"], str):
            acc.add(node["type"])
        for value in node.values():
            _schema_types(value, acc)
    elif isinstance(node, list):
        for entry in node:
            _schema_types(entry, acc)
    return acc


def test_every_schema_type_is_one_the_validator_implements():
    """The single-source-of-truth property, asserted from the direction that can actually
    break: a schema keyword the walker does not implement would silently accept anything."""
    from personalclaw.knowledge_providers.web_source import (
        FIELD_SCHEMA,
        POST_PROCESS_SCHEMA,
    )

    implemented = {
        "object",
        "array",
        "string",
        "integer",
        "boolean",
        "selector",
        "field",
        "post_process",
    }
    found = set()
    for schema in (SPEC_SCHEMA, FIELD_SCHEMA, POST_PROCESS_SCHEMA):
        _schema_types(schema, found)
    assert found <= implemented, f"unimplemented schema type(s): {sorted(found - implemented)}"


def test_an_unimplemented_schema_type_raises_rather_than_passing_everything():
    """The walker's guard is live, not decorative — this is what makes the test above mean
    something rather than measuring a set nobody enforces."""
    from personalclaw.knowledge_providers.web_source import _validate_against

    with pytest.raises(AssertionError):
        _validate_against("x", {"type": "number"}, "spec.x")


def test_the_detector_enum_in_the_schema_is_the_detector_stack():
    """A sixth detector cannot be added to the stack without the schema admitting it (and a
    schema entry for a detector that does not exist cannot linger)."""
    assert SPEC_SCHEMA["properties"]["detectors"]["items"]["enum"] == list(DETECTOR_ORDER)
    assert len(DETECTOR_ORDER) == 5
    assert DETECTOR_ORDER[-1] == DETECTOR_SELECTOR_FREQUENCY


def test_spec_validation_is_fail_closed(store):
    prov = WebSourceProvider(store)
    for bad in (
        {},
        {"url": ""},
        {"url": "file:///etc/passwd"},
        {"url": "not a url"},
        {"url": PAGE_URL, "max_items": 0},
        {"url": PAGE_URL, "max_items": MAX_ITEMS_PER_POLL + 1},
        {"url": PAGE_URL, "detectors": ["no_such_detector"]},
        {"url": PAGE_URL, "sanitize_html": "yes"},
        {"url": PAGE_URL, "typo_key": 1},
        {"url": PAGE_URL, "extraction": {"items": {}}},
        {"url": PAGE_URL, "extraction": {"items": {"selector": "div:has(> a)"}}},
        {"url": PAGE_URL, "extraction": {"items": {"selector": "div"}, "title": {"nope": 1}}},
        {
            "url": PAGE_URL,
            "extraction": {
                "items": {"selector": "div"},
                "title": {"selector": "h2", "extractor": "telepathy"},
            },
        },
        {
            "url": PAGE_URL,
            "extraction": {
                "items": {"selector": "div"},
                "title": {"selector": "h2", "post_process": [{"name": "exec"}]},
            },
        },
    ):
        ok, err = prov.validate_spec(bad)
        assert not ok and err, bad
    assert prov.validate_spec({"url": PAGE_URL})[0]
    assert prov.validate_spec(
        {"url": PAGE_URL, "detectors": [DETECTOR_JSON_LD], "extraction": _MANUAL_EXTRACTION}
    )[0]


@pytest.mark.asyncio
async def test_a_mutated_spec_is_refused_at_poll_time_not_only_at_save(store):
    """The spec is a mutable row an MCP tool or a hand-edit can change after the fact, so the
    guard runs on every poll — and here the stakes are the fetch TARGET."""
    fetcher = _Fetcher(_Resp(_changelog()))
    sid, _p, engine, _q = _setup(store, fetcher)
    store.db.execute(
        "UPDATE sources SET spec = ? WHERE id = ?",
        (json.dumps({"url": "file:///etc/passwd"}), sid),
    )
    store.db.commit()
    assert await _poll(engine, store, sid) == 0
    assert fetcher.requests == [], "a refused spec never reaches the fetch seam"
    assert "spec.url" in (store.get_source(sid)["last_error_summary"] or "")


# ── zero tokens in the detection path ───────────────────────────────────────────────


def _forbid_model_calls(monkeypatch):
    """Patch every model seam this repo routes background completions through to RAISE.

    A comment claiming "zero LLM" proves nothing and an assertion that a poll SUCCEEDED would
    pass with a model running, so the proof is that the seams are unreachable.
    """
    import personalclaw.llm_helpers as llm

    calls: list[str] = []

    def _boom(name):
        async def _seam(*a, **kw):
            calls.append(name)
            raise AssertionError(f"the detection path must not reach {name}")

        return _seam

    for seam in ("one_shot_completion", "stream_and_collect", "stream_and_collect_json"):
        monkeypatch.setattr(llm, seam, _boom(seam))
    return calls


@pytest.mark.asyncio
async def test_the_whole_detection_path_makes_zero_model_calls(store, monkeypatch):
    calls = _forbid_model_calls(monkeypatch)
    fetcher = _Fetcher(routes={"wp-json": _Resp(_WP_POSTS), "changelog": _Resp(_wordpress_page())})
    provider = WebSourceProvider(store, fetch_fn=fetcher, render_fn=_Render(_changelog()))

    for spec in (
        {"url": PAGE_URL},
        {"url": PAGE_URL, "detectors": [DETECTOR_JSON_STATE]},
        {"url": PAGE_URL, "extraction": _MANUAL_EXTRACTION},
    ):
        await provider.preview(spec, budget={"max_requests": 4, "allow_render": True})

    sid, _p, engine, queue = _setup(
        store,
        _Fetcher(_Resp(_js_shell())),
        budget={"allow_render": True},
        render=_Render(_changelog()),
    )
    assert await _poll(engine, store, sid) == 3
    assert calls == [], f"the detection path reached a model: {calls}"


@pytest.mark.asyncio
async def test_the_model_seams_really_do_raise_when_patched(store, monkeypatch):
    """The vacuity counterpart. Without it, a typo'd patch target (or a seam that moved)
    would make the zero-token test above a test of nothing at all."""
    import personalclaw.llm_helpers as llm

    _forbid_model_calls(monkeypatch)
    with pytest.raises(AssertionError):
        await llm.one_shot_completion("hello")
    with pytest.raises(AssertionError):
        await llm.stream_and_collect_json("hello")


def test_the_provider_imports_no_model_and_owns_no_socket():
    """Structural rails. The zero-token property and the one-fetch-seam property are both
    kept by ABSENCE, so they are asserted against the source text: a future edit that reaches
    for a completion or an HTTP client reds here rather than at review time."""
    from pathlib import Path

    import personalclaw.knowledge_providers.html_dom as dom_mod
    import personalclaw.knowledge_providers.web_source as mod

    for module in (mod, dom_mod):
        src = Path(module.__file__).read_text(encoding="utf-8")
        body = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
        for forbidden in ("one_shot_completion", "llm_helpers", "get_active_embed_fn"):
            assert forbidden not in body, f"{module.__name__} reaches for {forbidden}"
        for forbidden in ("aiohttp", "httpx", "urllib.request", "requests."):
            assert forbidden not in body, f"{module.__name__} owns a socket via {forbidden}"
    # Vacuity floor: the two names the provider IS allowed to route through must be present,
    # so the rail above is measuring absence-of-the-wrong-thing rather than an empty file.
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "from personalclaw.net.client import fetch" in src
    assert "from personalclaw.web.render import render_url" in src


def test_the_web_source_provider_ships_registered():
    """A `watched-page` source with no enrolled provider is a row nothing polls, so the
    registration is part of the feature rather than a wiring detail."""
    from pathlib import Path

    import personalclaw.dashboard.server as server

    src = Path(server.__file__).read_text(encoding="utf-8")
    assert "register_provider(WebSourceProvider(" in src


# ── the CSS subset behaves like CSS, or refuses ─────────────────────────────────────


def test_the_selector_subset_matches_and_refuses_predictably():
    from personalclaw.knowledge_providers.html_dom import SelectorError, parse_html, select

    dom = parse_html(
        '<html><body><div class="a b" id="one"><p class="x">1</p><span><p class="x">2</p>'
        "</span></div></body></html>"
    )
    assert len(select(dom, "p.x")) == 2
    assert len(select(dom, "div > p.x")) == 1, "child combinator is not descendant"
    assert len(select(dom, "#one p")) == 2
    assert len(select(dom, "div.a.b")) == 1
    assert len(select(dom, "div.a.missing")) == 0
    assert len(select(dom, "[id]")) == 1
    assert len(select(dom, '[id="one"]')) == 1
    assert len(select(dom, "span, div")) == 2
    for bad in ("div:has(p)", "p::first-line", "div + p", "div ~ p", "> p", "div >", ""):
        with pytest.raises(SelectorError):
            select(dom, bad)


def test_inner_html_round_trips_tags_and_attribute_values():
    from personalclaw.knowledge_providers.html_dom import parse_html, select_one

    dom = parse_html(
        '<html><body><div class="c">a <a href="/x?y=1&amp;z=2">b</a> c</div></body></html>'
    )
    el = select_one(dom, "div.c")
    assert el is not None
    assert el.inner_html == 'a <a href="/x?y=1&amp;z=2">b</a> c'
    assert el.text == "a b c"


def test_script_text_is_reachable_but_never_read_as_item_text():
    """The reason structural parsing runs on RAW markup: json_ld/json_state need script
    bodies, and item text must never contain them."""
    from personalclaw.knowledge_providers.html_dom import parse_html, select_one

    dom = parse_html(
        '<html><body><div class="c">Visible<script>var secret = 1;</script></div></body></html>'
    )
    el = select_one(dom, "div.c")
    assert el is not None
    assert el.text == "Visible"
    script = select_one(dom, "script")
    assert script is not None and "var secret = 1;" in script.raw_text
