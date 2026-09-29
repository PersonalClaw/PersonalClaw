"""A saved page, an uploaded HTML file and a mirrored document artifact are stored as their words.

The library shows an item's body as markdown. Three ways in still stored markup:

* a bookmark's scrape — the extractor's markdown turns a tag the page only SHOWED
  (``&lt;iframe …&gt;``) back into the characters of a real one;
* an uploaded ``.html`` file — the reader's html2text pass does the same;
* the artifact mirror — a ``document`` artifact's body is HTML, and it was mirrored verbatim.

Each now goes through the same conversion the feed path uses: HTML becomes markdown text, and
no raw HTML survives outside code. Bodies stored before that (a watched feed's or page's
entries, a document artifact's mirror) are converted once, keyed on what the body still
holds, so a second pass writes nothing.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.artifacts import changes
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.knowledge.artifact_ingest import ArtifactIndexer, ensure_source, find_source
from personalclaw.knowledge.connectors.base import without_raw_html
from personalclaw.knowledge.store import KnowledgeStore

#: What a page SHOWS as text about markup — entity-encoded, as any article about HTML writes it.
SHOWN_MARKUP = (
    "<p>Paste this and a page gets a nested document: "
    "&lt;iframe srcdoc=&quot;&lt;script&gt;alert(1)&lt;/script&gt;&quot;&gt;&lt;/iframe&gt;, and "
    "&lt;form action=&quot;https://collect.example.com&quot;&gt; collects what is typed. "
    "Handlers ride on images too: &lt;img src=x onerror=alert(2)&gt;.</p>"
)

#: Raw tag openers outside code — none may survive into a stored body.
RAW_TAGS = ("<iframe", "<form", "<img", "<script", "<p>", "<h1", "<div")


def _assert_stored_as_words(text: str) -> None:
    for tag in RAW_TAGS:
        assert tag not in text, (tag, text)
    assert without_raw_html(text) == text, "raw HTML survived outside code"
    assert "nested document" in text and "collects what is typed" in text


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _no_stray_listeners():
    before = list(changes._listeners)
    changes._listeners.clear()
    yield
    changes._listeners.clear()
    changes._listeners.extend(before)


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


# ── a bookmark's scrape ─────────────────────────────────────────────────────────


def _page(body: str) -> str:
    return (
        "<html><head><title>Notes on embedding</title></head><body><main><article>"
        f"<h1>Notes on embedding</h1>{body}"
        "<p>More prose follows, with enough words to be the main content of the page.</p>"
        "</article></main></body></html>"
    )


def _scrape(monkeypatch, html: str) -> str:
    import personalclaw.net as net
    import personalclaw.net.client as client
    from personalclaw.knowledge.connectors.web_url import WebUrlConnector

    async def fake_fetch(url, **kw):
        return client.FetchResponse(
            url=url, status=200, headers={"Content-Type": "text/html"}, body=html.encode()
        )

    monkeypatch.setattr(net, "fetch", fake_fetch)
    text, meta = asyncio.run(WebUrlConnector().fetch({"uri": "https://blog.example.com/embed"}))
    assert meta.get("error") is None, meta
    return text


def test_a_bookmarks_scrape_is_stored_as_its_words(monkeypatch):
    text = _scrape(monkeypatch, _page(SHOWN_MARKUP))
    _assert_stored_as_words(text)
    assert "&lt;iframe" in text, "the shown tag stays shown text"


def test_a_real_frame_on_the_page_does_not_reach_the_scrape(monkeypatch):
    text = _scrape(
        monkeypatch,
        _page(
            "<p>Before the frame, the page says nested document and collects what is typed.</p>"
            "<iframe srcdoc='<script>parent.alert(1)</script>'></iframe>"
        ),
    )
    _assert_stored_as_words(text)
    assert "srcdoc" not in text


def test_a_scrapes_code_block_is_left_as_code(monkeypatch):
    """The vacuity arm: inside code a tag is already text, and escaping it would garble it."""
    text = _scrape(
        monkeypatch,
        _page(
            "<p>It says nested document; it collects what is typed.</p>"
            "<pre><code>&lt;div class=&quot;card&quot;&gt;&lt;/div&gt;</code></pre>"
        ),
    )
    assert '<div class="card"></div>' in text, text


@pytest.mark.parametrize("way_in", ["a feed entry", "an uploaded file"])
def test_code_the_html_shows_stays_code(tmp_path, way_in):
    """html2text wrote a ``<pre>`` as an INDENTED block, which the no-raw-HTML step does not
    know for code — so the tag inside it was escaped and the page showed a literal ``&lt;div``
    in the code. A fenced block is code to both."""
    html = (
        "<p>The card is written like this:</p>"
        "<pre><code>&lt;div class=&quot;card&quot;&gt;&lt;/div&gt;</code></pre><p>Done.</p>"
    )
    if way_in == "a feed entry":
        from personalclaw.knowledge.connectors.base import readable_text

        text = readable_text(html)
    else:
        from personalclaw.knowledge.readers import FileReader

        path = tmp_path / "card.html"
        path.write_text(html, encoding="utf-8")
        text, _ = FileReader().read(str(path))
    assert '```\n<div class="card"></div>\n```' in text, text
    assert "&lt;div" not in text


# ── an uploaded HTML file ───────────────────────────────────────────────────────


def test_an_uploaded_html_file_is_read_as_its_words(tmp_path):
    from personalclaw.knowledge.readers import FileReader

    path = tmp_path / "saved.html"
    path.write_text(_page(SHOWN_MARKUP), encoding="utf-8")
    text, meta = FileReader().read(str(path))
    assert meta["format"] == "html"
    _assert_stored_as_words(text)


def test_the_upload_pipelines_reader_node_hands_over_words(tmp_path):
    """The node an uploaded document's ingest runs — what becomes the item's body."""
    from personalclaw.knowledge.pipeline.nodes.text_nodes import DocumentReadNode
    from personalclaw.knowledge.pipeline.types import NodeContext

    path = tmp_path / "saved.htm"
    path.write_text(_page(SHOWN_MARKUP), encoding="utf-8")
    out = asyncio.run(
        DocumentReadNode().run(
            {}, NodeContext(item_id="k1", item_type="document", file_path=str(path))
        )
    )
    assert out.success
    _assert_stored_as_words(out.text)


# ── the document-artifact mirror ────────────────────────────────────────────────


class _Queue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def enqueue(self, item_id: str) -> None:
        self.enqueued.append(item_id)


def _indexer(store, artifacts, queue) -> ArtifactIndexer:
    class _Cfg:
        auto_ingest_artifacts = True

    return ArtifactIndexer(
        store, enqueue=queue.enqueue, provider_factory=lambda: artifacts, config_loader=_Cfg
    )


def _mirror(store, slug: str) -> dict:
    source = find_source(store)
    assert source is not None
    row = store.find_source_item(str(source["id"]), slug)
    assert row is not None
    return row


EDITORIAL = (
    "<article><h1>Quarterly plan</h1><p>Ship the <strong>importer</strong> first.</p>"
    "<ul><li>nested document</li><li>collects what is typed</li></ul>"
    "<iframe srcdoc=\"<script>parent.document.title='x'</script>\"></iframe>"
    f"{SHOWN_MARKUP}</article>"
)


def test_a_document_artifact_is_mirrored_as_its_words(store, tmp_path):
    artifacts = NativeArtifactProvider(tmp_path / "artifacts")
    art = artifacts.create(name="Quarterly plan", content=EDITORIAL, kind="document")
    assert _indexer(store, artifacts, _Queue()).index(art.slug) == ArtifactIndexer.INDEXED
    row = _mirror(store, art.slug)
    content = row["content"]
    _assert_stored_as_words(content)
    assert "# Quarterly plan" in content
    assert "**importer**" in content
    assert "parent.document.title" not in content, "the real frame's document was kept"
    assert row["file_metadata"]["format"] == "html"


def test_a_document_artifact_holding_markdown_is_kept_as_written(store, tmp_path):
    """The other half of the sniff: a document saved with a markdown body is markdown, and an
    HTML parser would fold its lines into one paragraph."""
    body = "# Plan\n\n- first\n- second\n\nPress <kbd>Ctrl</kbd> to run it."
    artifacts = NativeArtifactProvider(tmp_path / "artifacts")
    art = artifacts.create(name="Plan", content=body, kind="document")
    assert _indexer(store, artifacts, _Queue()).index(art.slug) == ArtifactIndexer.INDEXED
    row = _mirror(store, art.slug)
    assert row["content"] == body
    assert row["file_metadata"]["format"] == "md"


def test_an_html_artifact_that_shows_markup_is_mirrored_as_its_words(store, tmp_path):
    artifacts = NativeArtifactProvider(tmp_path / "artifacts")
    art = artifacts.create(name="Explainer", content=_page(SHOWN_MARKUP), kind="html")
    assert _indexer(store, artifacts, _Queue()).index(art.slug) == ArtifactIndexer.INDEXED
    _assert_stored_as_words(_mirror(store, art.slug)["content"])


def test_a_mirror_stored_before_the_conversion_is_redone_once_at_start(store, tmp_path):
    """The mirror's own backfill runs only on the FIRST enable, so a home that mirrored a
    document artifact verbatim would keep it verbatim forever. Start re-mirrors exactly the
    rows whose stored text still holds HTML — once: the second start writes nothing."""
    from personalclaw.knowledge import artifact_ingest

    artifacts = NativeArtifactProvider(tmp_path / "artifacts")
    art = artifacts.create(name="Quarterly plan", content=EDITORIAL, kind="document")
    note = artifacts.create(
        name="Plain note", content="# Note\n\nnothing to convert", kind="markdown"
    )
    queue = _Queue()
    indexer = _indexer(store, artifacts, queue)
    ensure_source(store)
    assert indexer.index(art.slug) == ArtifactIndexer.INDEXED
    assert indexer.index(note.slug) == ArtifactIndexer.INDEXED
    # How a home mirrored the document before this change: its HTML verbatim, with the hash of
    # exactly that text — what the mirror wrote then.
    stale = _mirror(store, art.slug)
    legacy = artifact_ingest.redact(EDITORIAL)
    meta = dict(stale["file_metadata"])
    meta.update(
        artifact_sha=artifact_ingest.mirror_sha(stale["title"], legacy),
        format="md",
        extension=".md",
    )
    store.update_item(stale["id"], content=legacy, file_metadata=meta, touch=False)
    note_before = _mirror(store, note.slug)
    queue.enqueued.clear()

    assert indexer.remirror_markup() == 1
    assert queue.enqueued == [stale["id"]]
    _assert_stored_as_words(_mirror(store, art.slug)["content"])
    assert _mirror(store, note.slug)["updated_at"] == note_before["updated_at"]

    queue.enqueued.clear()
    assert indexer.remirror_markup() == 0, "a second start re-mirrored a converted row"
    assert queue.enqueued == []
    assert artifact_ingest.find_source(store) is not None


# ── a watched folder's HTML file ───────────────────────────────────────────────


def test_a_watched_folders_html_file_is_read_as_its_words(store, tmp_path):
    from personalclaw.knowledge_providers.dir_source import DirSourceProvider

    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "saved.html").write_text(_page(SHOWN_MARKUP), encoding="utf-8")
    (folder / "plain.md").write_text("# Plain\n\nPress <kbd>K</kbd>.", encoding="utf-8")
    provider = DirSourceProvider(store)
    spec = {"path": str(folder)}
    _assert_stored_as_words(provider._read(spec, "saved.html") or "")
    assert (
        provider._read(spec, "plain.md") == "# Plain\n\nPress <kbd>K</kbd>."
    ), "a markdown file is markdown, kept as written"


# ── bodies stored before the conversion ─────────────────────────────────────────


LEGACY_FEED_BODY = (
    "<h2>2.4.1 (17th May)</h2> <ul><li>Provide more context in <code>ParseError</code>.</li></ul>"
    "<p>Here&rsquo;s a small fix.</p><iframe srcdoc='<script>parent.alert(1)</script>'></iframe>"
)


def _legacy_feed_item(store, body: str, *, provider: str = "watched-feed") -> str:
    sid = store.create_source(
        name="releases",
        provider=provider,
        kind="feed" if provider == "watched-feed" else "web",
        spec={"kind": "rss", "url": "https://feeds.example.com/releases.atom"},
        item_type="bookmark",
    )
    item_id = store.create_typed_item(
        item_type="bookmark",
        title="Version 2.4.1",
        content=body,
        url="https://example.com/2.4.1",
        provider=provider,
        source_id=sid,
        guid="urn:example:241",
    )
    assert item_id
    store.add_extracted_content(item_id, "passthrough", backend="native", text=body)
    return item_id


def _pool_texts(store, item_id: str) -> list[str]:
    rows = store.db.execute(
        "SELECT text FROM extracted_contents WHERE item_id = ?", (item_id,)
    ).fetchall()
    return [r["text"] for r in rows]


@pytest.mark.parametrize("provider", ["watched-feed", "watched-page"])
def test_a_watched_items_stored_html_is_converted_once(store, provider):
    from personalclaw.knowledge import stored_markup

    item_id = _legacy_feed_item(store, LEGACY_FEED_BODY, provider=provider)
    store.db.execute("UPDATE items SET embedding = x'00' WHERE id = ?", (item_id,))
    store.db.commit()

    assert asyncio.run(stored_markup.convert_in_batches(store)) == 1
    item = store.get_item(item_id)
    content = item["content"]
    for markup in ("<h2>", "<ul>", "<li>", "<p>", "&rsquo;", "<iframe"):
        assert markup not in content, (markup, content)
    assert "2.4.1 (17th May)" in content and "`ParseError`" in content
    assert "Here's a small fix." in content
    assert _pool_texts(store, item_id) == [content], "the pool's copy of the body follows it"
    row = store.db.execute("SELECT embedding FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["embedding"] is None, "the vector of the old text is invalidated for a rebuild"
    # Searchable by its words, through the index the library's search reads.
    assert [r["title"] for r in store.search_items_fts("ParseError", limit=5)] == ["Version 2.4.1"]

    stamp = item["updated_at"]
    assert (
        asyncio.run(stored_markup.convert_in_batches(store)) == 0
    ), "a second pass rewrote a converted body"
    assert store.get_item(item_id)["updated_at"] == stamp


def test_the_conversion_touches_only_watched_sources(store):
    """A note a person wrote with HTML in it is hers, and it is never rewritten."""
    from personalclaw.knowledge import stored_markup

    note_id = store.create_typed_item(
        item_type="note", title="Mine", content="<p>My <b>own</b> words</p>"
    )
    assert asyncio.run(stored_markup.convert_in_batches(store)) == 0
    assert store.get_item(note_id)["content"] == "<p>My <b>own</b> words</p>"


def test_a_watched_item_already_stored_as_words_is_left_alone(store):
    """Text with no HTML outside code is not re-run through an HTML parser, which would fold
    its lines — a `<` in code or in prose is text already."""
    from personalclaw.knowledge import stored_markup

    body = "Line one.\n\nUse `Vec<T>` where a < b.\n\n```\n<div>code</div>\n```"
    item_id = _legacy_feed_item(store, body)
    assert asyncio.run(stored_markup.convert_in_batches(store)) == 0
    assert store.get_item(item_id)["content"] == body


def test_the_conversion_resumes_across_batches(store):
    from personalclaw.knowledge import stored_markup

    sid = store.create_source(
        name="releases",
        provider="watched-feed",
        kind="feed",
        spec={"kind": "rss", "url": "https://feeds.example.com/r.atom"},
        item_type="bookmark",
    )
    ids = [
        store.create_typed_item(
            item_type="bookmark",
            title=f"Entry {i}",
            content=f"<p>entry number {i}</p>",
            provider="watched-feed",
            source_id=sid,
            guid=f"urn:example:{i}",
        )
        for i in range(5)
    ]
    converted, cursor, batches = 0, 0, 0
    while cursor is not None:
        done, cursor = stored_markup.convert_batch(store, after_rowid=cursor, limit=2)
        converted += len(done)
        batches += 1
    assert converted == 5 and batches == 3
    assert [store.get_item(i)["content"] for i in ids] == [f"entry number {i}" for i in range(5)]
