"""Item identity for watched sources (WATCHED-SOURCES §3.3).

Two different questions, deliberately answered by two different functions, because
answering them with one key is how a dedup system either duplicates everything or
collapses unrelated stories:

* :func:`compose_guid` — *"is this the same item THIS source already gave me?"* The
  per-source novelty key the seen-set and the ``UNIQUE(source_id, guid)`` index are
  built on. Scoped to one source, so it can be as loose as the source's own notion of
  identity (a feed guid, a row id, a path).
* :func:`merge_key` — *"is this the same STORY a DIFFERENT source already gave me?"*
  The cross-source key, and the only thing in the system that can collapse two
  sightings into one item with two attributions.

Both are deterministic string work — never a model (§3.3's "URL/id matching is code,
not model" rule), so the same feed always yields the same identity, dedup costs zero
tokens, and nothing about it depends on a provider being reachable.

**The merge rule is canonicalized-URL equality, and nothing else.** Not title, not
title+date, not fuzzy similarity. Two feeds carrying one story agree on the link they
point at; they do NOT agree on the headline (an aggregator rewrites it, a newsletter
prefixes it, a mirror translates it), and two genuinely different stories published the
same day share a date. A wrong merge is strictly worse than a duplicate: a duplicate is
visible and one delete away, while a wrong merge silently destroys one of two distinct
items and stamps the survivor with an attribution that is a lie. So when identity is
ambiguous this module returns ``""`` — no merge key — and the engine keeps both items.

Two guards make that concrete:

* **No URL, no merge.** An item with no link (an "Ask HN" post, a CSV row with only a
  title) has no cross-source identity available, so it is always its own item.
* **A bare origin is not a story.** ``https://example.com`` canonicalizes from a feed
  whose entries all point at a site's homepage; merging on it would collapse every item
  from that site into one. A merge key requires a path or a query.
"""

from __future__ import annotations

import hashlib

#: Hard cap on a stored identity string. A feed can hand back an arbitrarily long guid
#: (a data: URL, a whole document as an id); the key is an index entry, not content.
MAX_KEY_CHARS = 512


def canonical_url(url: str) -> str:
    """The canonicalized form of *url*, or ``""`` when it is not an http(s) URL.

    Thin wrapper over the store's :func:`~personalclaw.knowledge.store.normalize_url`
    (lowercased host, tracking params stripped, fragment dropped, params sorted) —
    reused rather than re-derived so the key computed HERE is byte-identical to what
    ``create_typed_item`` writes into ``items.url``, which is what makes the merge
    lookup a single indexed equality instead of a scan-and-canonicalize.
    """
    from personalclaw.knowledge.store import normalize_url

    raw = (url or "").strip()
    if not raw:
        return ""
    canon = normalize_url(raw)
    # normalize_url returns junk (mailto:, relative, unparseable) UNCHANGED, so the
    # scheme check is what distinguishes "canonicalized" from "passed through".
    if not canon.lower().startswith(("http://", "https://")):
        return ""
    return canon[:MAX_KEY_CHARS]


def merge_key(url: str) -> str:
    """The cross-source merge key for an item, or ``""`` when it has none (§3.3).

    ``""`` means *"do not merge this"* — every caller must treat an empty key as "keep
    both items", never as "merge with everything else that also has no key" (that
    inversion would collapse an entire feed of link-less items into one row).
    """
    canon = canonical_url(url)
    if not canon:
        return ""
    from urllib.parse import urlsplit

    parts = urlsplit(canon)
    if not parts.query and not parts.path.strip("/"):
        # A bare origin. See the module docstring: this is a site, not a story.
        return ""
    return canon


def compose_guid(*, guid: str = "", url: str = "", title: str = "", published_at: str = "") -> str:
    """The per-source novelty key for one sighting (§3.3's composable guid).

    Cascade, most-authoritative first: the feed's own ``guid``/``id``, else the
    canonicalized URL, else ``sha256(title + published_at)[:16]``. Returns ``""`` when
    the item carries none of the three — a sighting with no derivable identity cannot be
    novelty-gated, so the provider must skip it rather than let it re-ingest on every
    poll (the storm the seen-set exists to prevent).

    The title+date hash is the LAST resort on purpose and is only ever used WITHIN one
    source: two different stories from one feed sharing a title and a timestamp is a
    tolerable collision, whereas using the same hash across sources is exactly the
    fuzzy-identity merge :func:`merge_key` refuses to make.
    """
    supplied = (guid or "").strip()
    if supplied:
        return supplied[:MAX_KEY_CHARS]
    canon = canonical_url(url)
    if canon:
        return canon
    basis = f"{(title or '').strip()}\n{(published_at or '').strip()}"
    if not basis.strip():
        return ""
    return hashlib.sha256(basis.encode("utf-8", errors="replace")).hexdigest()[:16]
