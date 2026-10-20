"""Text from outside is scanned before Knowledge keeps it, at every door that is not a file.

A watched source's entries (a feed's, a page's, an app's source such as a repository's files)
were stored as notes with nothing scanning them: the ingest scanned only what a document reader
read of a file, and a note has no file. The page the library fetches for a bookmark, a web watch's
new items, and an edit made to a page of the knowledge vault were kept the same way. So text from
outside sat in the library, its search and the prompts it is recalled into, unscanned.

Now that text is read by the content scan before anything is kept of it, by the rules an upload's
text is read by. What the scan refuses is a failed item that keeps no text and says the scan's
sentence, named in the gateway log, with the security event an upload's refusal records; the vault
leaves the note as it was and says why in the page. A scan that could not run keeps nothing
either, and a source's entry is read again when it is offered again. A source's text that changed
is scanned again; text that did not change is not read again.

Each test drives a real knowledge store and the real content scan, but for the one that counts how
many scans run at once. The refused text is the scanner's own example of content it refuses: a
shopping list with an invisible right-to-left override character in it. The same list without it
is ordinary content, and each refusal sits beside it.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from personalclaw.knowledge.source_engine import SourceEngine
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.knowledge_providers.base import (
    CHANGE_CREATED,
    CHANGE_MODIFIED,
    KnowledgeSourceProvider,
    SourceItem,
    SourcePollResult,
)
from personalclaw.sel import sel
from personalclaw.uploads import content_scan

#: The scanner's own example of content it refuses: a list with a right-to-left override in it.
OVERRIDE_LIST = "Shopping list for Saturday: \u202eeggs\u202c, flour, apples."
#: The same list without the override: ordinary content.
CLEAN_LIST = "Shopping list for Saturday: eggs, flour, apples."
#: The status line of an item whose text the content scan refused.
REFUSED = "Its text failed the content safety scan, so nothing was made from it."
#: The status line of an item whose text the content scan could not check.
UNCHECKED = "Its text could not be checked, so nothing was made from it."


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture
def store(tmp_path) -> KnowledgeStore:
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


@pytest.fixture
def scans(monkeypatch) -> list[bytes]:
    """Every text the content scan's child is asked to read, in order (it still reads them)."""
    asked: list[bytes] = []
    real = content_scan._ask_child

    async def _counted(window: bytes):
        asked.append(window)
        return await real(window)

    monkeypatch.setattr(content_scan, "_ask_child", _counted)
    return asked


class _Queue:
    """The gateway's ingest queue, as far as a poll uses it: what it was handed, in order."""

    def __init__(self) -> None:
        self.ids: list[str] = []

    def enqueue(self, item_id: str) -> None:
        self.ids.append(item_id)

    enqueue_background = enqueue

    def recover_pending(self) -> int:
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


class _Resp:
    def __init__(self, body: str, url: str) -> None:
        self.status = 200
        self.headers: dict = {}
        self.text = body
        self.url = url


def _engine(store: KnowledgeStore, provider, queue: _Queue) -> SourceEngine:
    return SourceEngine(store, queue, providers_lister=lambda: [provider], config_loader=_cfg)


async def _poll(engine: SourceEngine, store: KnowledgeStore, sid: str) -> int:
    return await engine.poll_source(store.get_source(sid), _cfg())


def _items(store: KnowledgeStore, sid: str) -> dict[str, dict]:
    rows = store.db.execute("SELECT id FROM items WHERE source_id = ?", (sid,)).fetchall()
    return {item["guid"]: item for item in (store.get_item(r["id"]) for r in rows)}


def _scan_rows() -> list[tuple[str, str, str]]:
    return [
        (r["caller_identity"], r["outcome"], r["resources"])
        for r in sel().recent(limit=200)
        if r.get("operation") == "upload_scan"
    ]


def _holds_none_of_it(item: dict, store: KnowledgeStore) -> None:
    """The item keeps no text of what the scan refused, anywhere it keeps text."""
    for field in ("title", "content", "summary"):
        assert "\u202e" not in str(item.get(field) or ""), (field, item.get(field))
    assert item["content"] == ""
    assert not store.get_extracted_contents(item["id"])


def _refused_steps(item: dict, reason: str) -> None:
    """No step of its graph runs, and each says why, rather than reading as still to come."""
    phases = (item.get("file_metadata") or {}).get("node_phases") or {}
    assert phases and {(p["status"], p["reason"]) for p in phases.values()} == {
        ("not_applicable", reason)
    }


# ── a feed ───────────────────────────────────────────────────────────────────


FEED_URL = "https://garden.example.com/club.rss"


def _rss(*entries: tuple[str, str, str]) -> str:
    """An RSS 2.0 feed of ``(guid, title, description)`` entries, each with its own link."""
    body = "".join(
        f"<item><guid>{guid}</guid><title>{title}</title>"
        f"<link>https://garden.example.com/{guid.rsplit(':', 1)[-1]}</link>"
        f"<description>{text}</description></item>"
        for guid, title, text in entries
    )
    return f"<?xml version='1.0'?><rss version='2.0'><channel>{body}</channel></rss>"


GARDEN_FEED = _rss(
    ("urn:garden:market", "Saturday market list", CLEAN_LIST),
    ("urn:garden:list", "Saturday shopping list", OVERRIDE_LIST),
)


def _feed(store: KnowledgeStore, body: str = GARDEN_FEED):
    from personalclaw.knowledge_providers.feed_source import FeedSourceProvider

    served = {"body": body}

    async def fetch(url, *, policy=None, headers=None):
        return _Resp(served["body"], url)

    sid = store.create_source(
        name="Garden club",
        provider="watched-feed",
        kind="feed",
        spec={"kind": "rss", "url": FEED_URL},
        item_type="bookmark",
    )
    queue = _Queue()
    return sid, _engine(store, FeedSourceProvider(store, fetch_fn=fetch), queue), queue, served


@pytest.mark.asyncio
async def test_a_feed_entry_the_scan_refuses_is_a_failed_item_that_keeps_no_text(store, caplog):
    sid, engine, queue, _ = _feed(store)

    with caplog.at_level(logging.WARNING, logger="personalclaw.knowledge.source_engine"):
        await _poll(engine, store, sid)

    items = _items(store, sid)
    # The ordinary entry is kept as it was written, and handed on to be read.
    kept = items["urn:garden:market"]
    assert kept["content"] == CLEAN_LIST and kept["title"] == "Saturday market list"
    assert kept["processing_status"] == "queued" and queue.ids == [kept["id"]]
    # The refused one is a failed item that says why and keeps no text, named by its link.
    refused = items["urn:garden:list"]
    assert (refused["processing_status"], refused["processing_error"]) == ("failed", REFUSED)
    assert refused["title"] == "https://garden.example.com/list"
    _holds_none_of_it(refused, store)
    _refused_steps(refused, REFUSED)
    # The security event an upload's refusal records, naming the door.
    assert _scan_rows() == [("uploads.content_scan:watched_source", "rejected", "extracted text")]
    # Said in the gateway's log too, naming the source and the entry, since no one was told.
    said = [r.getMessage() for r in caplog.records if REFUSED in r.getMessage()]
    assert len(said) == 1 and sid in said[0] and "urn:garden:list" in said[0], said

    from personalclaw.knowledge.pipeline.runner import ingest_item

    await ingest_item(store, kept["id"])
    await ingest_item(store, refused["id"])
    assert store.get_item(kept["id"])["content"] == CLEAN_LIST
    assert store.get_item(kept["id"])["processing_status"] != "failed"
    again = store.get_item(refused["id"])
    assert (again["processing_status"], again["processing_error"]) == ("failed", REFUSED)
    assert again["content"] == ""


@pytest.mark.asyncio
async def test_an_entry_already_taken_is_not_read_again_when_the_feed_offers_it_again(store, scans):
    sid, engine, queue, _ = _feed(store)

    await _poll(engine, store, sid)
    first = len(scans)
    before = _items(store, sid)
    # The feed answers its whole document again, as a feed does once anything in it changed.
    await _poll(engine, store, sid)

    assert first == 2, "each new entry was read once"
    assert len(scans) == first, "an entry it has taken is not read again"
    assert _items(store, sid) == before
    assert len(queue.ids) == 1


@pytest.mark.asyncio
async def test_a_new_entry_among_the_old_is_the_one_read(store, scans):
    sid, engine, _queue, served = _feed(store)
    await _poll(engine, store, sid)
    first = len(scans)

    served["body"] = _rss(
        ("urn:garden:seeds", "Seed swap on Sunday", "Bring seeds to swap."),
        ("urn:garden:market", "Saturday market list", CLEAN_LIST),
        ("urn:garden:list", "Saturday shopping list", OVERRIDE_LIST),
    )
    await _poll(engine, store, sid)

    assert len(scans) == first + 1
    assert _items(store, sid)["urn:garden:seeds"]["content"] == "Bring seeds to swap."


# ── a scan that could not run ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_scan_that_cannot_run_keeps_nothing_and_the_entry_is_read_when_offered_again(
    store,
):
    sid, engine, queue, _ = _feed(store, _rss(("urn:garden:market", "Market day", CLEAN_LIST)))

    with patch.object(content_scan, "scan_argv", return_value=["/nonexistent/pc-content-scan"]):
        await _poll(engine, store, sid)

    unchecked = _items(store, sid)["urn:garden:market"]
    assert (unchecked["processing_status"], unchecked["processing_error"]) == ("failed", UNCHECKED)
    assert unchecked["content"] == "" and queue.ids == []
    assert _scan_rows() == [("uploads.content_scan:watched_source", "error", "extracted text")]

    # Offered again with the scan running: the check it was owed is made, and it is kept.
    await _poll(engine, store, sid)

    read = _items(store, sid)["urn:garden:market"]
    assert read["id"] == unchecked["id"]
    assert read["content"] == CLEAN_LIST and read["processing_status"] == "queued"
    assert not (read.get("file_metadata") or {}).get("refused")
    assert queue.ids == [read["id"]]


# ── a page ───────────────────────────────────────────────────────────────────


PAGE_URL = "https://garden.example.com/news"


def _news_page() -> str:
    entries = "".join(
        f'<article><h2><a href="/news/{slug}">{title}</a></h2><p>{text}</p></article>'
        for slug, title, text in (
            ("market", "The Saturday market list", CLEAN_LIST),
            ("list", "The Saturday shopping list", OVERRIDE_LIST),
        )
    )
    return f"<html><head><title>News</title></head><body><main>{entries}</main></body></html>"


@pytest.mark.asyncio
async def test_a_watched_pages_entry_the_scan_refuses_is_refused_and_an_ordinary_one_kept(store):
    from personalclaw.knowledge_providers.web_source import WebSourceProvider

    async def fetch(url, *, policy=None, headers=None):
        return _Resp(_news_page(), url)

    sid = store.create_source(
        name="Garden news",
        provider="watched-page",
        kind="web_page",
        spec={"url": PAGE_URL},
        item_type="bookmark",
    )
    queue = _Queue()
    engine = _engine(store, WebSourceProvider(store, fetch_fn=fetch), queue)

    await _poll(engine, store, sid)

    by_title = {i["url"]: i for i in _items(store, sid).values()}
    kept = by_title[f"{PAGE_URL}/market"]
    assert CLEAN_LIST in kept["content"] and kept["processing_status"] == "queued"
    refused = by_title[f"{PAGE_URL}/list"]
    assert (refused["processing_status"], refused["processing_error"]) == ("failed", REFUSED)
    assert refused["title"] == f"{PAGE_URL}/list"
    _holds_none_of_it(refused, store)
    assert queue.ids == [kept["id"]]


# ── an app's source: a repository's files ────────────────────────────────────


class _RepoSource(KnowledgeSourceProvider):
    """An app's source of a repository's files, as the git-repo app is: each poll hands the
    text of the files that changed, keyed by their path."""

    def __init__(self) -> None:
        self.next: list[SourceItem] = []

    @property
    def name(self) -> str:
        return "repo-files"

    @property
    def display_name(self) -> str:
        return "Repository files"

    async def list_sources(self):
        return []

    async def search(self, query: str, limit: int = 10):
        return []

    async def get_item(self, item_id: str):
        return None

    async def poll(self, source_id: str, cursor: str = "") -> SourcePollResult:
        items, self.next = self.next, []
        return SourcePollResult(items=items, cursor=cursor + "+")


def _file(text: str, change: str = CHANGE_MODIFIED) -> SourceItem:
    return SourceItem(guid="notes/plan.md", title="notes/plan.md", content=text, change=change)


@pytest.mark.asyncio
async def test_an_apps_source_is_scanned_again_when_its_text_changes_and_not_when_it_does_not(
    store, scans
):
    repo = _RepoSource()
    sid = store.create_source(name="Plans", provider="repo-files", kind="external", spec={})
    queue = _Queue()
    engine = _engine(store, repo, queue)

    repo.next = [_file(CLEAN_LIST, CHANGE_CREATED)]
    assert await _poll(engine, store, sid) == 1
    item = _items(store, sid)["notes/plan.md"]
    assert item["content"] == CLEAN_LIST and queue.ids == [item["id"]]
    assert len(scans) == 1

    # The repository says the file changed, and its text is the same: nothing is read again.
    repo.next = [_file(CLEAN_LIST)]
    assert await _poll(engine, store, sid) == 0
    assert len(scans) == 1 and queue.ids == [item["id"]]

    # Its text changed to what the scan refuses: the same item keeps none of what it held.
    repo.next = [_file(OVERRIDE_LIST)]
    assert await _poll(engine, store, sid) == 1
    refused = _items(store, sid)["notes/plan.md"]
    assert refused["id"] == item["id"]
    assert (refused["processing_status"], refused["processing_error"]) == ("failed", REFUSED)
    _holds_none_of_it(refused, store)
    assert len(scans) == 2 and queue.ids == [item["id"]]

    # Changed again, to ordinary text: it is read, and kept again.
    repo.next = [_file(CLEAN_LIST + " And seed potatoes.")]
    assert await _poll(engine, store, sid) == 1
    again = _items(store, sid)["notes/plan.md"]
    assert again["id"] == item["id"]
    assert again["content"] == CLEAN_LIST + " And seed potatoes."
    assert again["processing_status"] == "queued" and again["processing_error"] is None
    assert not (again.get("file_metadata") or {}).get("refused")
    assert queue.ids == [item["id"], item["id"]]


# ── a page the library fetches for a bookmark ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [CLEAN_LIST, OVERRIDE_LIST], ids=["ordinary", "refused"])
async def test_a_bookmarks_page_is_scanned_before_anything_is_kept_of_it(store, page, caplog):
    from personalclaw.knowledge.connectors.web_url import WebUrlConnector
    from personalclaw.knowledge.pipeline.runner import ingest_item

    async def fetch(self, spec):
        return page, {"page_title": "Saturday at the market"}

    item_id = store.create_typed_item(
        item_type="bookmark",
        title="https://news.example.com/market",
        url="https://news.example.com/market",
        extra={"processing_status": "queued"},
    )
    with (
        patch.object(WebUrlConnector, "fetch", fetch),
        caplog.at_level(logging.WARNING, logger="personalclaw.knowledge.pipeline.runner"),
    ):
        await ingest_item(store, item_id)

    item = store.get_item(item_id)
    said = [r.getMessage() for r in caplog.records if REFUSED in r.getMessage()]
    if page == CLEAN_LIST:
        assert said == []
        assert item["content"] == CLEAN_LIST and item["processing_status"] != "failed"
        return
    assert (item["processing_status"], item["processing_error"]) == ("failed", REFUSED)
    _holds_none_of_it(item, store)
    assert item["title"] == "https://news.example.com/market", "the page's title is not kept"
    # The step that fetched the page says why it failed; no other step reads as done, since
    # nothing any of them made of the page is kept.
    phases = dict((item.get("file_metadata") or {}).get("node_phases") or {})
    step = phases.pop("bookmark_scrape", {})
    assert (step.get("status"), step.get("reason")) == ("failed", REFUSED)
    assert phases and {(p["status"], p["reason"]) for p in phases.values()} == {
        ("not_applicable", REFUSED)
    }
    assert _scan_rows() == [("uploads.content_scan:knowledge", "rejected", "extracted text")]
    # Said in the gateway's log, naming the item by its link, never by the page's text.
    assert len(said) == 1 and item_id in said[0] and "news.example.com/market" in said[0], said
    assert "\u202e" not in said[0]


@pytest.mark.asyncio
async def test_a_bookmark_holding_its_own_text_is_not_fetched_or_read_again(store, scans):
    """A bookmark she saved with text of her own passes it through, as before: nothing fetched."""
    from personalclaw.knowledge.connectors.web_url import WebUrlConnector
    from personalclaw.knowledge.pipeline.runner import ingest_item

    async def fetch(self, spec):
        raise AssertionError("a bookmark holding its own text fetches nothing")

    item_id = store.create_typed_item(
        item_type="bookmark",
        title="Market",
        content="Notes I took at the market.",
        url="https://news.example.com/market",
        extra={"processing_status": "queued"},
    )
    with patch.object(WebUrlConnector, "fetch", fetch):
        await ingest_item(store, item_id)

    assert store.get_item(item_id)["content"] == "Notes I took at the market."
    assert scans == []


# ── a web watch's new items ──────────────────────────────────────────────────


def test_a_web_watchs_new_item_the_scan_refuses_is_kept_as_nothing(store, caplog):
    from personalclaw.triggers.web_poll import _route_to_knowledge

    watch = SimpleNamespace(id="trg-garden", name="Garden news")
    page = "https://garden.example.com/board"

    with caplog.at_level(logging.WARNING, logger="personalclaw.triggers.web_poll"):
        written = _route_to_knowledge(watch, page, [CLEAN_LIST, OVERRIDE_LIST], store)

    assert written == 2
    rows = store.db.execute("SELECT id FROM items WHERE provider = 'web_watch'").fetchall()
    items = [store.get_item(r["id"]) for r in rows]
    kept = [i for i in items if i["processing_status"] != "failed"]
    refused = [i for i in items if i["processing_status"] == "failed"]
    assert [i["title"] for i in kept] == [CLEAN_LIST]
    (one,) = refused
    assert one["processing_error"] == REFUSED and one["title"] == page
    _holds_none_of_it(one, store)
    assert _scan_rows() == [("uploads.content_scan:web_watch", "rejected", "extracted text")]
    said = [r.getMessage() for r in caplog.records if REFUSED in r.getMessage()]
    assert len(said) == 1 and "trg-garden" in said[0] and "\u202e" not in said[0], said


def test_a_web_watchs_new_items_are_each_scanned_a_few_at_a_time(store, monkeypatch):
    """A poll has no cap on how many new items it brings, so they are scanned several at once
    rather than one after another, never more than the bound at a time, and each one is read."""
    import asyncio

    from personalclaw.knowledge import text_items
    from personalclaw.triggers.web_poll import _route_to_knowledge

    running, peak, asked = 0, 0, []

    async def _child(window: bytes):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        asked.append(window)
        return {"dangerous": False}

    monkeypatch.setattr(content_scan, "_ask_child", _child)
    items = [f"Seed swap number {i} on Sunday" for i in range(3 * text_items.SCANS_AT_ONCE)]
    watch = SimpleNamespace(id="trg-garden", name="Garden news")

    written = _route_to_knowledge(watch, "https://garden.example.com/board", items, store)

    assert written == len(items)
    assert sorted(asked) == sorted(i.encode() for i in items)
    assert peak == text_items.SCANS_AT_ONCE


# ── an edit made in a page of the knowledge vault ────────────────────────────


def _two_way_vault(store: KnowledgeStore):
    """The library as a vault of pages she can edit, which Knowledge reads back."""
    import json

    from personalclaw.config.loader import config_dir
    from personalclaw.knowledge import vault as kv

    home = config_dir()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(
        json.dumps({"knowledge": {"vault_mode": "two_way"}}), encoding="utf-8"
    )
    vault = kv.vault_for(store)
    assert vault is not None
    return vault


def _edit(page, old: str, new: str) -> None:
    """Edit a page as an editor saves it: its text changes, and its time moves on."""
    import os
    import time

    page.write_text(page.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    stamp = time.time() + 2
    os.utime(page, (stamp, stamp))


def _said_in_page(page) -> str:
    from personalclaw.memory_vault import CONFLICT_KEY, parse_frontmatter, split_page

    return str(parse_frontmatter(split_page(page.read_text(encoding="utf-8"))[0]).get(CONFLICT_KEY))


@pytest.mark.parametrize(
    ("scan", "said", "row"),
    [
        (None, "its text failed the content safety scan, so the item was not changed", "rejected"),
        (
            ["/nonexistent/pc-content-scan"],
            "its text could not be checked, so the item was not changed",
            "error",
        ),
    ],
    ids=["refused", "unchecked"],
)
def test_an_edit_made_in_a_vault_page_is_scanned_before_its_note_takes_it(
    store, caplog, scan, said, row
):
    """A page is a file any program on the machine can write. An edit the scan refuses, or could
    not check, leaves the note as it was and says why in the page; an ordinary one is taken in."""
    from personalclaw.knowledge import vault as kv

    item_id = store.create_typed_item(item_type="note", title="Saturday", content=CLEAN_LIST)
    vault = _two_way_vault(store)
    vault.sync_batch()
    page = vault.path / f"items/{kv.page_basename(store.get_item(item_id))}.md"

    _edit(page, CLEAN_LIST, OVERRIDE_LIST if scan is None else CLEAN_LIST + " And beans.")
    argv = patch.object(content_scan, "scan_argv", return_value=scan) if scan else None
    with caplog.at_level(logging.WARNING, logger="personalclaw.knowledge.vault"):
        if argv is not None:
            with argv:
                result = vault.sync_batch()
        else:
            result = vault.sync_batch()

    assert (result["absorbed"], result["conflicts"]) == (0, 1)
    assert store.get_item(item_id)["content"] == CLEAN_LIST
    assert _said_in_page(page) == said
    assert _scan_rows() == [("uploads.content_scan:knowledge_vault", row, "extracted text")]
    logged = [r.getMessage() for r in caplog.records if "was not taken in" in r.getMessage()]
    assert len(logged) == 1 and item_id in logged[0] and "\u202e" not in logged[0], logged

    # Saved again with ordinary text, the page is read back into its note.
    _edit(page, OVERRIDE_LIST if scan is None else "And beans.", "And seed potatoes.")
    result = vault.sync_batch()

    assert result["absorbed"] == 1
    assert store.get_item(item_id)["content"].strip().endswith("And seed potatoes.")
