"""Watched feeds — the feed-source provider (WATCHED-SOURCES §3).

The kind for endpoints that are ALREADY structured: RSS 2.0, Atom, JSON Feed, JSON APIs,
and CSV-with-header. There is nothing to detect and nothing to guess here (that is the
web-source's job, §2) — a feed states its own items, so this provider is pure parsing plus
conditional GET, and it costs zero tokens by construction.

**Three parsers, not five formats.** RSS and Atom differ only in element names, so one XML
parser handles both by sniffing the root tag. JSON Feed, HN Algolia and the GitHub recipe
are all "a JSON document containing a list of objects", so they share ONE parser driven by
a declarative field map. CSV is that same field map over header names. Adding a feed
shape is therefore a dict, not a branch.

**Presets are recipes, not code paths (§3.1).** ``hn_algolia`` and ``github_trending`` are
entries in :data:`PRESETS` — a partial spec (url + items path + field map) that a source's
own spec overrides key by key. So a user pointing at an unusual JSON API writes the same
kind of spec the bundled presets are made of, and a preset can be fixed without touching
this module. Nothing about a preset is privileged.

**Conditional GET is the whole cost story (§3.2).** The cursor carries the ETag and
Last-Modified the server last handed back, and every poll offers them as
``If-None-Match`` / ``If-Modified-Since``. A feed that has not changed answers 304 with no
body: the poll is a few hundred bytes, no parse, no items, and the validators are kept so
the next poll is equally cheap. This is what makes a 15-minute interval polite rather than
abusive, and it is the reason the provider must never "just re-fetch to be safe".

**Identity is composed, never invented (§3.3).** A feed that supplies a guid owns its own
identity; one that does not gets the canonicalized URL, and failing that a
``sha256(title + published_at)`` — the cascade in
:mod:`personalclaw.knowledge.source_identity`. An item from which no identity can be
derived at all is DROPPED rather than emitted, because an un-keyable item would re-ingest
on every single poll: the seen-set can only gate what it can name.

**Never a raise to the engine, never a socket of its own.** Every failure path returns a
soft :class:`SourcePollResult` error so one broken feed degrades to a health status instead
of killing the shared loop, and every byte comes from ``net.fetch`` under the engine-owned
``SOURCE`` egress policy — host classification, private-IP denial and per-redirect-hop
re-checks all live there. ``fetch_fn`` is the single injectable seam so a test drives a
recorded feed body without a network.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any

from personalclaw.knowledge_providers.base import (
    KnowledgeItem,
    KnowledgeSource,
    KnowledgeSourceProvider,
    SourceItem,
    SourcePollResult,
)

logger = logging.getLogger(__name__)

#: Parser families. ``rss`` covers RSS 2.0 AND Atom (one XML parser, root-tag sniffed);
#: ``json`` covers JSON Feed and any JSON API via a field map; ``csv`` covers a
#: header-row export. A spec's ``kind`` must be one of these — an unknown kind is refused
#: at validate time rather than silently parsed as something else.
FEED_KINDS = frozenset({"rss", "json", "csv"})

#: Hard ceiling on items taken from ONE poll. The engine applies its own
#: ``max_items_per_poll`` on top; this bounds the parse itself, so a feed that returns
#: 50,000 rows cannot make one poll dominate the loop before the engine ever sees it.
MAX_ITEMS_PER_POLL = 200

#: Per-item content ceiling. A feed carrying full article bodies is normal; a feed
#: carrying a megabyte per entry is a pathology that would blow the poll's memory.
MAX_ITEM_CHARS = 200_000

#: Bundled source recipes (§3.1). Each is a partial spec the source's own spec overrides
#: key-by-key, so a preset is data — never a code branch — and a user can start from one
#: and change the URL, the item path or a single field mapping.
PRESETS: dict[str, dict[str, Any]] = {
    # A plain feed URL; the XML parser sniffs RSS vs Atom from the root element, so
    # "atom" is the same recipe and is aliased below.
    "rss": {"kind": "rss"},
    "atom": {"kind": "rss"},
    # JSON Feed 1.1 (jsonfeed.org).
    "json_feed": {
        "kind": "json",
        "items_path": "items",
        "fields": {
            "guid": ["id"],
            "title": ["title"],
            "url": ["url", "external_url"],
            "content": ["content_text", "content_html", "summary"],
            "published_at": ["date_published", "date_modified"],
        },
    },
    # Hacker News via the Algolia search API. `search_by_date` is the polling shape (newest
    # first); `objectID` is a stable per-story id, so the guid never needs composing.
    # A self/Ask post carries no `url`, so the permalink template supplies one — unique per
    # story, therefore never a false cross-source merge, while still giving the item a link.
    "hn_algolia": {
        "kind": "json",
        "url": "https://hn.algolia.com/api/v1/search_by_date?tags=story",
        "items_path": "hits",
        "permalink_template": "https://news.ycombinator.com/item?id={guid}",
        "fields": {
            "guid": ["objectID"],
            "title": ["title", "story_title"],
            "url": ["url", "story_url"],
            "content": ["story_text", "comment_text"],
            "published_at": ["created_at"],
        },
    },
    # GitHub publishes NO trending API (the trending page is HTML with no feed), so this
    # recipe approximates it deterministically with the search API sorted by stars. Being a
    # recipe rather than code is the point: a user who wants a different window or language
    # filter overrides `url` in their own spec and keeps the field map.
    "github_trending": {
        "kind": "json",
        "url": (
            "https://api.github.com/search/repositories"
            "?q=stars%3A%3E1000&sort=stars&order=desc&per_page=30"
        ),
        "items_path": "items",
        "fields": {
            "guid": ["node_id", "id"],
            "title": ["full_name", "name"],
            "url": ["html_url"],
            "content": ["description"],
            "published_at": ["pushed_at", "updated_at"],
        },
    },
}

#: Field map used when a ``csv`` spec names none — the common export column names.
DEFAULT_CSV_FIELDS: dict[str, list[str]] = {
    "guid": ["id", "guid"],
    "title": ["title", "name", "full_name"],
    "url": ["url", "link", "html_url"],
    "content": ["description", "summary", "content", "body"],
    "published_at": ["published", "published_at", "date", "created_at", "updated_at"],
}

#: Atom's namespace. RSS 2.0 has none, which is exactly how the two are told apart.
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def resolve_spec(spec: dict | None) -> dict:
    """A source's spec with its preset's defaults underneath (§3.1).

    The source's OWN keys win, so a preset is a starting point rather than a constraint —
    overriding ``url`` on ``hn_algolia`` (to poll a query instead of the front page) leaves
    the field map intact, which is the whole value of presets-as-data.
    """
    spec = dict(spec or {})
    preset = str(spec.pop("preset", "") or "").strip()
    base = dict(PRESETS.get(preset, {}))
    base.update({k: v for k, v in spec.items() if v not in (None, "")})
    return base


def _pick(obj: dict, paths: Any) -> str:
    """First non-empty value among ``paths`` (a key, a dotted path, or a list of either).

    Dotted so a nested API shape (``author.name``) is reachable from a declarative map
    without needing a code branch per feed.
    """
    if isinstance(paths, str):
        paths = [paths]
    for path in paths or []:
        cur: Any = obj
        for part in str(path).split("."):
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(part)
        if isinstance(cur, (int, float)) and not isinstance(cur, bool):
            return str(cur)
        if isinstance(cur, str) and cur.strip():
            return cur.strip()
    return ""


def _clip(text: str) -> str:
    return (text or "")[:MAX_ITEM_CHARS]


def _text(el: Any) -> str:
    """All text under an XML element, flattened (an RSS description may carry markup)."""
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


class FeedSourceProvider(KnowledgeSourceProvider):
    """Poll-capable provider over a structured feed endpoint (§3).

    Spec keys (after preset resolution — see :func:`resolve_spec`):

    ``kind``       one of :data:`FEED_KINDS` (required)
    ``url``        the endpoint to poll (required)
    ``items_path`` dotted path to the item list in a JSON document (default ``items``)
    ``fields``     field → key/dotted-path/list-of-paths map for ``json``/``csv``
    ``max_items``  per-poll cap, clamped to :data:`MAX_ITEMS_PER_POLL`

    ``fetch_fn`` is the injectable fetch seam (defaults to ``net.fetch``) so tests drive a
    recorded body with no socket; it is the ONLY way bytes enter this provider, which is
    what keeps every request under the engine's ``SOURCE`` egress policy.
    """

    #: Feeds are cheap to poll conditionally (§3.2), so 15 minutes is polite rather than
    #: abusive — and the engine still clamps this up to its own network floor.
    poll_interval_seconds = 900

    def __init__(self, store: Any, *, fetch_fn=None, now_fn=None) -> None:
        self._store = store
        self._fetch_fn = fetch_fn
        import time

        self._now_fn = now_fn or time.time

    @property
    def name(self) -> str:
        return "watched-feed"

    @property
    def display_name(self) -> str:
        return "Watched Feed"

    # ── corpus contract (the library itself owns search/get) ────────────────────────

    async def list_sources(self) -> list[KnowledgeSource]:
        return [
            KnowledgeSource(id=s["id"], name=s["name"], source_type="feed", provider=self.name)
            for s in self._store.list_sources()
            if s.get("provider") == self.name
        ]

    async def search(self, query: str, limit: int = 10) -> list[KnowledgeItem]:
        # Polled items land in the library, so the library's own search covers them; a
        # second search path here would be a divergent ranking of the same rows.
        return []

    async def get_item(self, item_id: str) -> KnowledgeItem | None:
        return None

    # ── spec validation (save time AND poll time) ──────────────────────────────────

    def validate_spec(self, spec: dict) -> tuple[bool, str]:
        """Validate a feed spec: known kind, http(s) URL, sane cap. Fail-CLOSED.

        Run at the top of every :meth:`poll` as well as on save, for the reason
        ``dir_source`` documents: the spec is a mutable row an MCP tool or a hand-edit can
        change after the fact, so a save-only guard is one edit away from being bypassed.
        """
        resolved = resolve_spec(spec)
        kind = str(resolved.get("kind") or "").strip().lower()
        if kind not in FEED_KINDS:
            return False, f"feed kind must be one of {sorted(FEED_KINDS)}, got {kind!r}"
        url = str(resolved.get("url") or "").strip()
        if not url:
            return False, "feed source requires a 'url' (or a preset that supplies one)"
        if not url.lower().startswith(("http://", "https://")):
            # Only http(s) — a file:// or data: "feed" would read local disk through a
            # path that has none of pathguard's guarantees.
            return False, "feed url must be http(s)"
        raw_cap = resolved.get("max_items")
        # `or` would swallow 0 as "unset" and silently accept a cap of zero — the
        # defaulted-field-hides-an-invalid-input shape. Presence is checked explicitly.
        cap = MAX_ITEMS_PER_POLL if raw_cap in (None, "") else int(raw_cap)
        if cap < 1 or cap > MAX_ITEMS_PER_POLL:
            return False, f"max_items must be between 1 and {MAX_ITEMS_PER_POLL}"
        return True, ""

    # ── conditional GET (§3.2) ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_cursor(cursor: str) -> dict[str, str]:
        """The persisted validators. A corrupt cursor degrades to "no validators" — one
        full fetch, never a lost feed."""
        try:
            data = json.loads(cursor) if cursor else {}
        except (TypeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: str(v) for k, v in data.items() if isinstance(v, str) and v}

    @staticmethod
    def _conditional_headers(state: dict[str, str]) -> dict[str, str]:
        headers = {"Accept": "*/*"}
        if state.get("etag"):
            headers["If-None-Match"] = state["etag"]
        if state.get("last_modified"):
            headers["If-Modified-Since"] = state["last_modified"]
        return headers

    async def _fetch(self, url: str, *, policy: Any, headers: dict[str, str]) -> Any:
        """The one place bytes enter this provider. Defaults to the guarded
        ``net.fetch``; ``fetch_fn`` replaces it in tests so no test opens a socket."""
        if self._fetch_fn is not None:
            return await self._fetch_fn(url, policy=policy, headers=headers)
        from personalclaw.net.client import fetch

        return await fetch(url, policy=policy, headers=headers)

    # ── parsing ────────────────────────────────────────────────────────────────────

    def _parse_xml(self, body: str) -> list[dict[str, str]]:
        """RSS 2.0 or Atom → normalized field dicts (root tag decides which).

        Rejects a document declaring a DOCTYPE before parsing. ``xml.etree`` does not
        resolve external entities, but it does expand internal ones, which is the
        "billion laughs" amplification; no legitimate feed needs a DTD, so refusing one
        outright is cheaper and safer than depending on an optional hardened parser.
        """
        import xml.etree.ElementTree as ET

        head = body[:4096].lower()
        if "<!doctype" in head or "<!entity" in head:
            raise ValueError("feed declares a DOCTYPE/ENTITY and was refused")
        root = ET.fromstring(body)  # noqa: S314 — DOCTYPE refused above; no external entities
        out: list[dict[str, str]] = []
        if root.tag.startswith(_ATOM_NS) or root.tag == "feed":
            ns = _ATOM_NS if root.tag.startswith(_ATOM_NS) else ""
            for entry in root.iter(f"{ns}entry"):
                link = ""
                for le in entry.findall(f"{ns}link"):
                    rel = le.get("rel") or "alternate"
                    if rel == "alternate" and le.get("href"):
                        link = le.get("href") or ""
                        break
                    link = link or (le.get("href") or "")
                out.append(
                    {
                        "guid": _text(entry.find(f"{ns}id")),
                        "title": _text(entry.find(f"{ns}title")),
                        "url": link,
                        "content": _text(entry.find(f"{ns}content"))
                        or _text(entry.find(f"{ns}summary")),
                        "published_at": _text(entry.find(f"{ns}published"))
                        or _text(entry.find(f"{ns}updated")),
                    }
                )
            return out
        for node in root.iter("item"):
            guid_el = node.find("guid")
            guid = _text(guid_el)
            if guid_el is not None and (guid_el.get("isPermaLink") or "").lower() == "false":
                # An explicitly non-permalink guid is still a perfectly good identity; it
                # just must not be mistaken for the item's URL.
                pass
            out.append(
                {
                    "guid": guid,
                    "title": _text(node.find("title")),
                    "url": _text(node.find("link")),
                    "content": _text(node.find("description")),
                    "published_at": _text(node.find("pubDate")),
                }
            )
        return out

    def _parse_json(self, body: str, spec: dict) -> list[dict[str, str]]:
        """A JSON document → normalized field dicts via the spec's declarative field map."""
        data = json.loads(body)
        rows: Any = data
        for part in str(spec.get("items_path") or "items").split("."):
            if isinstance(rows, dict):
                rows = rows.get(part)
        if rows is None and isinstance(data, list):
            rows = data  # a bare top-level array (some APIs)
        if not isinstance(rows, list):
            raise ValueError("feed items path did not resolve to a list")
        fields = spec.get("fields") or PRESETS["json_feed"]["fields"]
        out: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append({key: _pick(row, paths) for key, paths in fields.items()})
        return out

    def _parse_csv(self, body: str, spec: dict) -> list[dict[str, str]]:
        """A header-row CSV → normalized field dicts (the githubsignals export shape)."""
        fields = spec.get("fields") or DEFAULT_CSV_FIELDS
        reader = csv.DictReader(io.StringIO(body))
        out: list[dict[str, str]] = []
        for row in reader:
            clean = {
                str(k).strip(): str(v).strip()
                for k, v in row.items()
                if k is not None and v is not None
            }
            out.append({key: _pick(clean, paths) for key, paths in fields.items()})
        return out

    def parse(self, body: str, spec: dict) -> list[dict[str, str]]:
        kind = str(spec.get("kind") or "").strip().lower()
        if kind == "rss":
            return self._parse_xml(body)
        if kind == "json":
            return self._parse_json(body, spec)
        return self._parse_csv(body, spec)

    # ── the poll ───────────────────────────────────────────────────────────────────

    def _to_item(self, row: dict[str, str], spec: dict) -> SourceItem | None:
        """One parsed row → a sighting, or None when it has no derivable identity.

        Dropping an un-keyable row is deliberate: the seen-set can only gate what it can
        name, so emitting one would re-ingest it on every poll forever — the storm the
        novelty gate exists to prevent, arriving as an identity bug rather than a feed one.
        """
        from personalclaw.knowledge.source_identity import compose_guid

        url = (row.get("url") or "").strip()
        title = (row.get("title") or "").strip()
        published = (row.get("published_at") or "").strip()
        guid = compose_guid(
            guid=row.get("guid") or "", url=url, title=title, published_at=published
        )
        if not guid:
            return None
        template = str(spec.get("permalink_template") or "")
        if not url and template and "{guid}" in template:
            url = template.replace("{guid}", guid)
        return SourceItem(
            guid=guid,
            title=title or url or guid,
            content=_clip(row.get("content") or ""),
            url=url,
            published_at=published,
            metadata={"feed_kind": str(spec.get("kind") or "")},
        )

    async def poll(
        self, source_id: str, cursor: str = "", *, policy: Any = None
    ) -> SourcePollResult:
        """One conditional fetch + parse. Never raises to the engine (§1.1).

        A 304 returns zero items and KEEPS the validators, which is the cheap steady state
        a feed should spend almost all its polls in. Any other failure is reported as a soft
        error with the cursor preserved, so the next poll retries from the same position
        rather than re-reading the whole feed.
        """
        source = self._store.get_source(source_id)
        if source is None:
            return SourcePollResult(error=f"source {source_id} no longer exists")
        ok, err = self.validate_spec(source.get("spec") or {})
        if not ok:
            return SourcePollResult(error=err)
        spec = resolve_spec(source.get("spec") or {})
        state = self._parse_cursor(cursor)
        try:
            resp = await self._fetch(
                str(spec["url"]), policy=policy, headers=self._conditional_headers(state)
            )
        except Exception as exc:  # noqa: BLE001 — egress denial, timeout, DNS: all soft
            return SourcePollResult(cursor=cursor, error=f"fetch failed: {exc}"[:200])

        status = int(getattr(resp, "status", 0) or 0)
        if status == 304:
            # Unchanged since our validators. Cursor returned verbatim so the SAME
            # validators are offered next time; a 304 that dropped them would turn every
            # subsequent poll into a full download.
            return SourcePollResult(items=[], cursor=cursor)
        if status >= 400 or status == 0:
            return SourcePollResult(cursor=cursor, error=f"feed returned HTTP {status}")

        headers = getattr(resp, "headers", None) or {}
        new_state = {
            k: v
            for k, v in (
                ("etag", str(headers.get("ETag") or headers.get("etag") or "")),
                (
                    "last_modified",
                    str(headers.get("Last-Modified") or headers.get("last-modified") or ""),
                ),
            )
            if v
        }
        try:
            rows = self.parse(getattr(resp, "text", "") or "", spec)
        except Exception as exc:  # noqa: BLE001 — a malformed feed is a soft failure
            logger.debug("feed %s parse failed", source_id, exc_info=True)
            return SourcePollResult(cursor=cursor, error=f"parse failed: {exc}"[:200])

        cap = min(int(spec.get("max_items") or MAX_ITEMS_PER_POLL), MAX_ITEMS_PER_POLL)
        items: list[SourceItem] = []
        dropped = 0
        for row in rows[:cap]:
            sighting = self._to_item(row, spec)
            if sighting is None:
                dropped += 1
                continue
            items.append(sighting)
        if dropped:
            logger.debug("feed %s: %d row(s) had no derivable identity", source_id, dropped)
        result = SourcePollResult(items=items, cursor=json.dumps(new_state, sort_keys=True))
        if not items and dropped:
            # Every row unkeyable is a real misconfiguration (wrong field map / wrong
            # items_path), not an empty feed — say so instead of reporting a healthy poll.
            result.error = f"{dropped} feed row(s) had no id, url or title to key on"
        return result
