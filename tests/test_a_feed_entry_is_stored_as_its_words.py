"""A watched feed's or page's entries are stored as their words, never as their markup.

Measured in the library: every feed item previewed as ``<h2>2.4.1 (17th May)</h2>
<h3>Added</h3> <ul> <li>…`` and ``<p>Here&rsquo;s a small…``, because an Atom entry's HTML
content (and an RSS description, a JSON Feed ``content_html``, a WordPress excerpt) was stored
verbatim as the item's body. That body is also what the item page renders as markdown with
inline HTML passed through, so storing someone else's markup verbatim put it one render away
from the app's own page. The entry is now converted to markdown text on the way in, and the
conversion leaves no raw HTML: a tag the entry only SHOWED (``&lt;iframe…&gt;``) comes out of
html2text as a real tag, so it is written back as text.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.knowledge.source_engine import SourceEngine
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.knowledge_providers.feed_source import FeedSourceProvider


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


class _Resp:
    def __init__(self, body):
        self.status = 200
        self.headers = {}
        self.text = body
        self.url = "https://feeds.example.com/releases.atom"


class _Queue:
    def enqueue(self, item_id):
        pass

    def enqueue_background(self, item_id):
        pass

    def recover_pending(self):
        return 0


def _cfg():
    from personalclaw.config.loader import SourcesConfig

    return SourcesConfig(
        enabled=True,
        poll_interval_default_secs=1,
        network_floor_secs=0,
        max_sources=100,
        max_items_per_poll=50,
    )


def _escape_xml(html: str) -> str:
    return html.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _atom(*entries: tuple[str, str]) -> str:
    """An Atom feed whose entries carry ``type="html"`` content, escaped as Atom requires."""
    body = "".join(
        f"<entry><id>urn:example:{i}</id><title type='html'>{_escape_xml(title)}</title>"
        f"<link href='https://example.com/{i}'/><updated>2026-09-29T08:00:00Z</updated>"
        f"<content type='html'>{_escape_xml(html)}</content></entry>"
        for i, (title, html) in enumerate(entries)
    )
    return f"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>{body}</feed>"


async def _poll_one(store, body: str, spec: dict | None = None) -> dict:
    async def fetch(url, *, policy=None, headers=None):
        return _Resp(body)

    sid = store.create_source(
        name="releases",
        provider="watched-feed",
        kind="feed",
        spec=spec or {"kind": "rss", "url": "https://feeds.example.com/releases.atom"},
        item_type="bookmark",
    )
    provider = FeedSourceProvider(store, fetch_fn=fetch)
    engine = SourceEngine(store, _Queue(), providers_lister=lambda: [provider], config_loader=_cfg)
    assert await engine.poll_source(store.get_source(sid), _cfg()) >= 1
    rows = store.db.execute(
        "SELECT title, content FROM items WHERE source_id = ? ORDER BY guid", (sid,)
    ).fetchall()
    return [dict(r) for r in rows]


@pytest.mark.asyncio
async def test_an_atom_entrys_html_is_stored_as_readable_text(store):
    (row,) = await _poll_one(
        store,
        _atom(
            (
                "Version 2.4.1",
                "<h2>2.4.1 (17th May)</h2> <h3>Added</h3> <ul> <li>Provide more context in "
                "some <code>ParseError</code> messages. (<a class='issue-link' "
                "href='https://example.com/project/pull/42'>#42</a>)</li></ul>"
                "<p>Here&rsquo;s a small fix.</p>",
            )
        ),
    )
    content = row["content"]
    for markup in ("<h2>", "<h3>", "<ul>", "<li>", "<p>", "&rsquo;", "class="):
        assert markup not in content, (markup, content)
    assert "2.4.1 (17th May)" in content
    assert "`ParseError`" in content
    assert "Here's a small fix." in content


@pytest.mark.asyncio
async def test_markup_an_entry_carries_or_shows_never_reaches_the_library_as_markup(store):
    """A real iframe in the entry is dropped; one the entry only SHOWED as text stays text."""
    (row,) = await _poll_one(
        store,
        _atom(
            (
                "Security notes",
                "<p>Before</p><iframe srcdoc='<script>parent.alert(1)</script>'></iframe>"
                "<p>Shown: &lt;img src=x onerror=alert(2)&gt; and "
                "&lt;script&gt;x&lt;/script&gt;</p>",
            )
        ),
    )
    content = row["content"]
    assert "<iframe" not in content and "srcdoc" not in content
    assert "<img" not in content and "<script" not in content
    assert "&lt;img src=x onerror=alert(2)>" in content, "the shown tag stays shown text"


@pytest.mark.asyncio
async def test_an_entrys_title_is_plain_text(store):
    (row,) = await _poll_one(
        store, _atom(("Why pipes get &quot;stuck&quot; &amp; <b>how</b>", "x"))
    )
    assert row["title"] == 'Why pipes get "stuck" & how'


@pytest.mark.asyncio
async def test_a_plain_text_entry_is_kept_as_it_was(store):
    """The vacuity arm: text with no markup is not run through an HTML parser, which would
    fold its line breaks into one paragraph."""
    body = json.dumps(
        {
            "items": [
                {
                    "id": "p1",
                    "title": "Plain",
                    "url": "https://example.com/p1",
                    "content_text": "Line one.\n\nLine two, with a & b < c.",
                }
            ]
        }
    )
    (row,) = await _poll_one(
        store,
        body,
        spec={"preset": "json_feed", "url": "https://feeds.example.com/feed.json"},
    )
    assert row["content"] == "Line one.\n\nLine two, with a & b < c."


def test_a_page_detectors_markup_is_stored_as_its_words_too():
    """The web-page source's items go through the same hygiene: a WordPress excerpt is the
    post's rendered HTML, and it was stored as it came."""
    from personalclaw.knowledge_providers.web_source import apply_hygiene

    (item,) = apply_hygiene(
        [
            {
                "title": "Release notes for the spring update",
                "url": "https://blog.example.com/spring",
                "content": "<p>Don&#8217;t miss <strong>this</strong>.</p>",
            }
        ],
        page_url="https://blog.example.com/",
        spec={},
    )
    assert item.content == "Don't miss **this**."


def test_no_raw_html_survives_outside_code_and_code_is_left_alone():
    from personalclaw.knowledge.connectors.base import readable_text, without_raw_html

    md = "Say <b>hi</b> `Vec<T>` <https://example.com/x>\n\n```\n<div>code</div>\n```\n<!-- c -->"
    out = without_raw_html(md)
    assert "&lt;b>hi&lt;/b>" in out
    assert "`Vec<T>`" in out, "inside a code span a tag is already text"
    assert "<https://example.com/x>" in out, "an autolink is a link, not a tag"
    assert "```\n<div>code</div>\n```" in out
    assert "&lt;!-- c -->" in out
    assert readable_text("a < b and <3") == "a < b and <3"
