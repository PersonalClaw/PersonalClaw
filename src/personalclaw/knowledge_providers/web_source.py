"""Watch this URL — the web-source provider (WATCHED-SOURCES §2).

The kind for pages that are NOT already a feed. A feed states its own items
(``feed_source.py``); a page has to be read. This module does that reading with five
deterministic detectors, a declarative escape hatch when all five miss, and an escalation to
the render tier when the page turns out to be a JavaScript shell — and it costs **zero
tokens** at every step, by construction rather than by policy. Nothing here imports a model,
a prompt or a completion; the detection path is string and tree work only.

**Five detectors, tried in reliability order (§2.1).** Each answers "does this page DECLARE
its items?" before the next one guesses harder:

``wordpress_api``      a ``<link rel="https://api.w.org/">`` names a WP REST endpoint — real
                       structured posts, one extra request, no scraping at all.
``json_ld``            Schema.org ``ItemList``/``blogPost`` blobs: the page telling a machine
                       what its items are.
``semantic_html``      HTML5 ``<article>`` elements, else a repeated ``<section>``/``<li>``
                       group under ``<main>``.
``json_state``         an SPA's own state blob (``__NEXT_DATA__``, ``__NUXT__``,
                       ``application/json`` scripts) walked for arrays of title+url objects.
``selector_frequency`` structural frequency analysis — the most repeated link-bearing block
                       signature on the page.

``selector_frequency`` runs LAST, which is the one place this module reorders §2.1's table.
It is the only detector that infers a structure the page never declared, so letting it beat
``json_state`` would let a heuristic outrank a declaration. Every detector is on by default
and individually switchable through ``spec.detectors``.

**Hygiene is applied per detector, not once at the end.** A detector whose every candidate
fails the §2.2 floors (off-domain link dropped, sub-three-word title rejected, nothing left
to key on) counts as having found NOTHING and the stack falls through to the next one. Doing
it the other way round — first detector to produce any raw candidate wins — is how a page's
navigation menu becomes "items".

**The listing-page rule is the primary remediation (§2.1).** Auto-detection works on LISTING
pages: changelogs, category/tag/archive/newsroom pages. It does not work on a homepage or a
single post, and when a source yields nothing the first thing to fix is the URL, not the
selectors. So zero items is never a bare empty result — it carries
:data:`LISTING_PAGE_GUIDANCE`.

**Escalation is decided by OUTCOME, not by status (§2.3).** A 200 whose detectors extract
nothing, on a page measurably built client-side (:func:`looks_like_js_shell`), is the JS-shell
signal — the plain fetch saw the empty shell. Then, and only then, the render tier
(``web/render.py``, which pre-flights the same egress guard because a browser bypasses IP
pinning) gets one attempt. ``budget.allow_render`` defaults **false**, and a source that needs
the tier without permission degrades to the ``needs render tier`` health status
rather than reporting a healthy empty poll forever. Every attempt in one poll — tier 1, the
WordPress sub-request, the render — draws on ONE ``budget.max_requests``.

**One seam for every byte.** ``fetch_fn`` defaults to ``net.fetch`` (host classification,
private-IP denial, per-hop redirect re-checks, byte cap) under the engine-supplied ``SOURCE``
egress policy; ``render_fn`` defaults to ``web.render.render_url``. Tier 2 is that path or
nothing: no per-source proxy, no UA rotation, no anti-bot mode. A source that would need those
is a source to drop.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from html import unescape
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from personalclaw.knowledge_providers import conditional_get
from personalclaw.knowledge_providers.base import (
    HEALTH_NEEDS_RENDER,
    KnowledgeItem,
    KnowledgeSource,
    KnowledgeSourceProvider,
    SourceItem,
    SourcePollResult,
    SourcePreview,
)
from personalclaw.knowledge_providers.html_dom import (
    Element,
    SelectorError,
    parse_html,
    parse_selector,
    select,
    select_one,
)

logger = logging.getLogger(__name__)

DETECTOR_WORDPRESS_API = "wordpress_api"
DETECTOR_JSON_LD = "json_ld"
DETECTOR_SEMANTIC_HTML = "semantic_html"
DETECTOR_JSON_STATE = "json_state"
DETECTOR_SELECTOR_FREQUENCY = "selector_frequency"

#: The stack, in the order it is tried. See the module docstring for why
#: ``selector_frequency`` is last rather than fourth.
DETECTOR_ORDER: tuple[str, ...] = (
    DETECTOR_WORDPRESS_API,
    DETECTOR_JSON_LD,
    DETECTOR_SEMANTIC_HTML,
    DETECTOR_JSON_STATE,
    DETECTOR_SELECTOR_FREQUENCY,
)

#: The detector name reported when a user's own ``spec.extraction`` produced the items. Not a
#: member of :data:`DETECTOR_ORDER`: a manual config REPLACES the stack rather than joining
#: it, so the preview can say plainly which of the two paths the user is looking at.
DETECTOR_MANUAL = "manual"

#: §2.3 per-poll request budget. Ten covers a tier-1 fetch, a WP sub-request and a render
#: with room to spare; the point is that a poll can never become a crawl.
DEFAULT_MAX_REQUESTS = 10

#: §2.2 output hygiene. A title of one or two words is a navigation label ("Home", "Sign up",
#: "Read more"), not an item headline — the single most effective filter against a homepage's
#: chrome being mistaken for content.
DEFAULT_MIN_WORDS_TITLE = 3

#: §2.2 — links off the page's own domain are ads, share buttons and recommendation widgets.
KEEP_DIFFERENT_DOMAIN_DROP = "drop"
KEEP_DIFFERENT_DOMAIN_KEEP = "keep"

#: §2.1 tunables for ``selector_frequency``: a signature must repeat at least this often to be
#: a candidate item block, and only this many of the most frequent signatures are considered.
MINIMUM_SELECTOR_FREQUENCY = 2
USE_TOP_SELECTORS = 5

#: Below this many characters of visible text, a page carrying scripts is a shell rather than
#: a document. MEASURED discrimination, not a guess about the URL: it is what lets the
#: provider tell "you pointed at a homepage" (lots of text, no items) apart from "this page
#: needs JavaScript" (almost no text, no items) — two failures with completely different
#: remediations that would otherwise both surface as "found nothing".
JS_SHELL_MAX_TEXT_CHARS = 400

#: Per-poll and per-item ceilings, mirroring ``feed_source``: one pathological page must not
#: dominate the shared loop or the process's memory.
MAX_ITEMS_PER_POLL = 200
MAX_ITEM_CHARS = 200_000

#: §2.1's failure-diagnosis UX, carried over verbatim in intent: the FIRST remediation for an
#: empty extraction is a better input URL.
LISTING_PAGE_GUIDANCE = (
    "No items found. Auto-detection reads LISTING pages — a changelog, a blog index, a "
    "category/tag/archive page, a newsroom. It cannot read a homepage or a single post. Try "
    "the page that lists the entries you want to watch, and only then a manual selector "
    "config (spec.extraction)."
)

#: The remediation when the page is measurably a JS shell.
RENDER_TIER_GUIDANCE = (
    "This page builds its content with JavaScript, so a plain fetch sees an empty shell. "
    "Set budget.allow_render to true to let this source use the render tier."
)

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
#: SPA state globals worth walking, in the order they are looked for.
_STATE_GLOBALS = ("__NEXT_DATA__", "__NUXT__", "__INITIAL_STATE__", "__APOLLO_STATE__", "STATE")
_TITLE_KEYS = ("title", "headline", "name", "subject")
_URL_KEYS = ("url", "href", "link", "permalink", "path", "slug")
_CONTENT_KEYS = ("description", "summary", "excerpt", "abstract", "content", "body", "text")
_DATE_KEYS = ("published_at", "datePublished", "date", "created_at", "publishedAt", "pubDate")
_ID_KEYS = ("id", "guid", "uuid", "objectID", "_id")


# ── the spec schema: ONE source of truth for validation (§2.2) ──────────────────────

#: The web-source spec, as a JSON Schema subset.
#:
#: §2.2 asks for a schema and the runtime validators to be one artifact so the FE form and an
#: agent can validate client-side without drifting from what the provider enforces. This is
#: that artifact, and the DIRECTION of derivation is deliberate: :func:`validate_spec` is a
#: generic walker OVER this schema rather than hand-written checks a generator later mirrors
#: into a schema. Deriving a schema from imperative validators produces a second artifact that
#: can drift; interpreting the schema means there is no second artifact to drift from. The
#: cost is that only the keywords the walker implements may appear here, which
#: ``test_web_source.py`` asserts.
SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["url"],
    "additionalProperties": False,
    "properties": {
        "url": {
            "type": "string",
            "pattern": r"^https?://",
            "description": "The listing page to watch.",
        },
        "detectors": {
            "type": "array",
            "items": {"type": "string", "enum": list(DETECTOR_ORDER)},
            "description": "Which detectors to run (default: all, in order).",
        },
        "max_items": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_ITEMS_PER_POLL,
            "description": "Cap on items taken from one poll.",
        },
        "min_words_title": {
            "type": "integer",
            "minimum": 0,
            "maximum": 20,
            "description": "Reject titles shorter than this many words (navigation chrome).",
        },
        "keep_different_domain": {
            "type": "string",
            "enum": [KEEP_DIFFERENT_DOMAIN_DROP, KEEP_DIFFERENT_DOMAIN_KEEP],
            "description": "What to do with items linking off the page's own domain.",
        },
        "sanitize_html": {
            "type": "boolean",
            "description": "Sanitize extracted HTML before it is stored (default true).",
        },
        "extraction": {
            "type": "object",
            "additionalProperties": False,
            "description": "Manual selector config; replaces the detector stack when present.",
            "properties": {
                "items": {
                    "type": "object",
                    "required": ["selector"],
                    "additionalProperties": False,
                    "properties": {"selector": {"type": "selector"}},
                },
                "title": {"type": "field"},
                "description": {"type": "field"},
                "url": {"type": "field"},
                "author": {"type": "field"},
                "guid": {"type": "field"},
                "published_at": {"type": "field"},
            },
        },
    },
}

#: The per-field extraction sub-schema (§2.2). Named separately because ``type: "field"``
#: above is the walker's marker for it — the schema stays flat enough to read while the
#: recursion stays in one place.
FIELD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "selector": {"type": "selector"},
        "extractor": {
            "type": "string",
            "enum": ["text", "html", "href", "attribute", "static"],
        },
        "attribute": {"type": "string"},
        "value": {"type": "string"},
        "post_process": {"type": "array", "items": {"type": "post_process"}},
    },
}

#: The post-process step sub-schema (§2.2's chain).
POST_PROCESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name"],
    "properties": {
        "name": {
            "type": "string",
            "enum": [
                "gsub",
                "html_to_markdown",
                "parse_time",
                "parse_uri",
                "sanitize_html",
                "substring",
                "template",
            ],
        },
        "pattern": {"type": "string"},
        "replacement": {"type": "string"},
        "start": {"type": "integer", "minimum": 0, "maximum": MAX_ITEM_CHARS},
        "end": {"type": "integer", "minimum": 0, "maximum": MAX_ITEM_CHARS},
        "string": {"type": "string"},
    },
}

_SUB_SCHEMAS = {"field": FIELD_SCHEMA, "post_process": POST_PROCESS_SCHEMA}


def _validate_against(value: Any, schema: dict[str, Any], path: str) -> str:
    """Check ``value`` against a schema node; return an error message or ``""``.

    Supports exactly the keywords :data:`SPEC_SCHEMA` uses — ``type`` (with the two named
    sub-schemas and the ``selector`` pseudo-type), ``enum``, ``required``, ``properties``,
    ``additionalProperties``, ``items``, ``minimum``/``maximum``, ``pattern``. An unknown
    ``type`` is a programming error and raises, so a schema keyword nobody implements can
    never silently pass everything (the shape of bug a permissive validator hides).
    """
    kind = schema.get("type")
    if kind in _SUB_SCHEMAS:
        return _validate_against(value, _SUB_SCHEMAS[str(kind)], path)
    if kind == "selector":
        if not isinstance(value, str) or not value.strip():
            return f"{path} must be a non-empty CSS selector"
        try:
            parse_selector(value)
        except SelectorError as exc:
            return f"{path}: {exc}"
        return ""
    if kind == "object":
        if not isinstance(value, dict):
            return f"{path} must be an object"
        for key in schema.get("required", []):
            present = value.get(key)
            # A required key present but blank is missing: an empty `url` is not a spec that
            # happens to lack a target, it is a spec that would fetch nothing on a timer.
            if key not in value or (isinstance(present, str) and not present.strip()):
                return f"{path}.{key} is required"
        props: dict[str, Any] = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(props))
            if unknown:
                return f"{path}: unknown key(s) {unknown}"
        for key, sub in props.items():
            if key in value:
                err = _validate_against(value[key], sub, f"{path}.{key}")
                if err:
                    return err
        return ""
    if kind == "array":
        if not isinstance(value, list):
            return f"{path} must be an array"
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, entry in enumerate(value):
                err = _validate_against(entry, item_schema, f"{path}[{i}]")
                if err:
                    return err
        return ""
    if kind == "string":
        if not isinstance(value, str):
            return f"{path} must be a string"
        enum = schema.get("enum")
        if enum is not None and value not in enum:
            return f"{path} must be one of {sorted(enum)}, got {value!r}"
        pattern = schema.get("pattern")
        if pattern and not re.match(str(pattern), value, re.IGNORECASE):
            return f"{path} must match {pattern}"
        return ""
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{path} must be an integer"
        low, high = schema.get("minimum"), schema.get("maximum")
        if low is not None and value < int(low):
            return f"{path} must be >= {low}"
        if high is not None and value > int(high):
            return f"{path} must be <= {high}"
        return ""
    if kind == "boolean":
        if not isinstance(value, bool):
            return f"{path} must be true or false"
        return ""
    raise AssertionError(f"SPEC_SCHEMA uses unimplemented type {kind!r} at {path}")


# ── the request budget (§2.3) ───────────────────────────────────────────────────────


@dataclass
class _Collected:
    """One collect pass: what was extracted, how, and at what cost.

    ``not_modified`` is kept distinct from "no items" on purpose — a 304 is a healthy poll of
    an unchanged page, and reporting the listing-page guidance for it would tell a perfectly
    working source to change its URL every cycle.
    """

    items: list[SourceItem] = dataclass_field(default_factory=list)
    detector: str = ""
    escalations: list[str] = dataclass_field(default_factory=list)
    guidance: str = ""
    health_status: str = ""
    error: str = ""
    cursor_state: dict[str, str] = dataclass_field(default_factory=dict)
    not_modified: bool = False


@dataclass
class _Budget:
    """One poll's request allowance, shared by every tier.

    ONE counter for tier 1, the WordPress sub-request and the render, because §2.3's budget
    is per POLL: a per-tier allowance would let an escalating page make three separate
    "small" budgets add up to a crawl.
    """

    max_requests: int
    used: int = 0

    def take(self) -> bool:
        if self.used >= self.max_requests:
            return False
        self.used += 1
        return True

    @property
    def spent(self) -> bool:
        return self.used >= self.max_requests


# ── post-process chain (§2.2) ───────────────────────────────────────────────────────


def sanitize_markup(html: str) -> str:
    """§2.2's default-ON sanitizer, reusing ``web/extract.py``'s nh3 path so there is one
    sanitizer in the codebase rather than a second opinion about what is safe markup."""
    from personalclaw.web.extract import sanitize_html as _sanitize

    return _sanitize(html)


def _html_to_markdown(html: str) -> str:
    from personalclaw.knowledge.connectors.base import html_to_text

    return html_to_text(html).strip()


def _parse_time(value: str) -> str:
    """Normalize a date to ISO-8601, or return it unchanged.

    Unchanged rather than empty on failure: the raw string is still a usable ``published_at``
    for display and for :func:`compose_guid`'s title+date fallback, whereas dropping it would
    change an item's identity for no gain.
    """
    from datetime import datetime

    raw = (value or "").strip()
    if not raw:
        return ""
    candidates = (raw, raw.replace("Z", "+00:00"))
    for text in candidates:
        try:
            return datetime.fromisoformat(text).isoformat()
        except ValueError:
            pass
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        return raw


def apply_post_process(value: str, steps: list[dict], *, page_url: str) -> str:
    """Run §2.2's post-process chain over one extracted value, in order.

    Each step is data validated by :data:`POST_PROCESS_SCHEMA`, so an unknown name never
    reaches here; a step that raises on pathological input (a catastrophic ``gsub`` pattern)
    leaves the value as it was rather than failing the whole item, because one bad rule in a
    six-field config should cost that field, not the poll.
    """
    out = value
    for step in steps or []:
        name = str(step.get("name") or "")
        try:
            if name == "gsub":
                out = re.sub(
                    str(step.get("pattern") or ""), str(step.get("replacement") or ""), out
                )
            elif name == "html_to_markdown":
                out = _html_to_markdown(out)
            elif name == "parse_time":
                out = _parse_time(out)
            elif name == "parse_uri":
                out = urljoin(page_url, out.strip()) if out.strip() else ""
            elif name == "sanitize_html":
                out = sanitize_markup(out)
            elif name == "substring":
                start = int(step.get("start") or 0)
                end = step.get("end")
                out = out[start : int(end)] if end is not None else out[start:]
            elif name == "template":
                out = str(step.get("string") or "").replace("{value}", out)
        except Exception:  # noqa: BLE001 — see the docstring: one field, not the poll
            logger.debug("post_process step %r failed", name, exc_info=True)
    return out


# ── raw candidate rows ──────────────────────────────────────────────────────────────


def _row(
    *,
    title: str = "",
    url: str = "",
    content: str = "",
    published_at: str = "",
    guid: str = "",
    author: str = "",
) -> dict[str, str]:
    return {
        "title": title.strip(),
        "url": url.strip(),
        "content": content.strip(),
        "published_at": published_at.strip(),
        "guid": guid.strip(),
        "author": author.strip(),
    }


def _first_link(el: Element) -> str:
    """The first descendant ``href`` worth calling an item's link.

    Prefers a link inside a heading — on a card, the headline's anchor is the item, while the
    first anchor in document order is often a category badge or an author avatar.
    """
    for heading in (h for h in el.iter_descendants() if h.tag in _HEADINGS):
        anchor = heading.find(frozenset({"a"}))
        if anchor is not None and anchor.attrs.get("href"):
            return anchor.attrs["href"]
    if el.tag == "a" and el.attrs.get("href"):
        return el.attrs["href"]
    for anchor in (a for a in el.iter_descendants() if a.tag == "a"):
        href = anchor.attrs.get("href") or ""
        if href and not href.startswith("#"):
            return href
    return ""


def _heading_text(el: Element) -> str:
    for node in el.iter_descendants():
        if node.tag in _HEADINGS:
            text = node.text
            if text:
                return text
    return ""


def _time_text(el: Element) -> str:
    for node in el.iter_descendants():
        if node.tag == "time":
            return node.attrs.get("datetime") or node.text
    return ""


def _block_row(el: Element) -> dict[str, str]:
    """One candidate block → a raw row. Shared by the two markup detectors so a ``<section>``
    and an ``<article>`` are read identically."""
    title = _heading_text(el)
    link = _first_link(el)
    text = el.text
    if title and text.startswith(title):
        text = text[len(title) :].strip()
    return _row(title=title, url=link, content=text, published_at=_time_text(el))


# ── detector: json_ld ───────────────────────────────────────────────────────────────


def _json_scripts(dom: Element, *, mime: str) -> list[str]:
    out: list[str] = []
    for node in dom.iter_descendants():
        if node.tag != "script":
            continue
        if (node.attrs.get("type") or "").strip().lower() != mime:
            continue
        body = node.raw_text.strip()
        if body:
            out.append(body)
    return out


def _ld_rows(blob: Any) -> list[dict[str, str]]:
    """Rows from one JSON-LD document, accepting only LIST-shaped declarations.

    A page declaring a single ``Article`` is a post, not a listing, and treating it as one
    item would make every blog post its own one-item "source" — and would defeat the
    homepage guidance, since a marketing homepage happily declares a single ``WebSite``. So
    the accepted shapes are the ones that mean "here are my entries":
    ``itemListElement``, ``blogPost``, ``hasPart``, or a bare top-level array.
    """
    if isinstance(blob, list):
        entries: list[Any] = list(blob)
    elif isinstance(blob, dict):
        graph = blob.get("@graph")
        if isinstance(graph, list):
            rows: list[dict[str, str]] = []
            for node in graph:
                rows.extend(_ld_rows(node))
            return rows
        entries = []
        for key in ("itemListElement", "blogPost", "hasPart"):
            found = blob.get(key)
            if isinstance(found, list):
                entries = found
                break
        if not entries:
            return []
    else:
        return []
    out: list[dict[str, str]] = []
    for entry in entries:
        node = entry
        if isinstance(node, dict) and isinstance(node.get("item"), dict):
            node = node["item"]  # ListItem wrapping the real thing
        if not isinstance(node, dict):
            continue
        url = _pick_key(node, _URL_KEYS)
        if not url:
            main = node.get("mainEntityOfPage")
            if isinstance(main, dict):
                url = str(main.get("@id") or "")
        out.append(
            _row(
                title=_pick_key(node, _TITLE_KEYS),
                url=url,
                content=_pick_key(node, _CONTENT_KEYS),
                published_at=_pick_key(node, _DATE_KEYS),
                guid=str(node.get("@id") or ""),
            )
        )
    return out


def _pick_key(obj: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        val = obj.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return str(val)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            nested = val.get("name") or val.get("@id") or val.get("url")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return ""


def detect_json_ld(dom: Element, **_: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for body in _json_scripts(dom, mime="application/ld+json"):
        try:
            rows.extend(_ld_rows(json.loads(body)))
        except (TypeError, ValueError):
            logger.debug("json_ld: unparseable ld+json block", exc_info=True)
    return rows


# ── detector: semantic_html ─────────────────────────────────────────────────────────


def detect_semantic_html(dom: Element, **_: Any) -> list[dict[str, str]]:
    """``<article>`` elements, else a repeated ``<section>``/``<li>`` group inside ``<main>``.

    The sibling-group fallback demands :data:`MINIMUM_SELECTOR_FREQUENCY` members that each
    carry BOTH a heading and a link. A marketing homepage's sections (a hero with one call to
    action, a features block with no links) do not clear that, which is exactly the point: the
    fallback must find a repeated *pattern*, not merely a container.
    """
    articles = [el for el in dom.iter_descendants() if el.tag == "article"]
    if articles:
        return [_block_row(el) for el in articles]
    best: list[dict[str, str]] = []
    for main in (el for el in dom.iter_descendants() if el.tag in ("main", "body")):
        for parent in [main, *main.iter_descendants()]:
            for tag in ("section", "li"):
                group = [c for c in parent.children if c.tag == tag]
                qualified = [c for c in group if _heading_text(c) and _first_link(c)]
                if len(qualified) >= MINIMUM_SELECTOR_FREQUENCY and len(qualified) > len(best):
                    best = [_block_row(c) for c in qualified]
        if best:
            return best
    return best


# ── detector: json_state ────────────────────────────────────────────────────────────


def _state_blobs(dom: Element) -> list[Any]:
    """Every parseable state document on the page, most-declarative first."""
    out: list[Any] = []
    for body in _json_scripts(dom, mime="application/json"):
        try:
            out.append(json.loads(body))
        except (TypeError, ValueError):
            continue
    decoder = json.JSONDecoder()
    for node in dom.iter_descendants():
        if node.tag != "script" or (node.attrs.get("type") or "").lower() == "application/json":
            continue
        body = node.raw_text
        for name in _STATE_GLOBALS:
            idx = body.find(name)
            if idx < 0:
                continue
            brace = body.find("{", idx)
            if brace < 0:
                continue
            try:
                # raw_decode stops at the end of the first complete value, so trailing
                # JavaScript after the assignment is simply not read.
                blob, _ = decoder.raw_decode(body[brace:])
            except ValueError:
                continue
            out.append(blob)
    return out


def _candidate_arrays(blob: Any, depth: int = 0) -> list[list[dict]]:
    """Arrays of objects that look like items (a title AND a url), shallowest first."""
    if depth > 8:
        return []
    found: list[list[dict]] = []
    if isinstance(blob, list):
        objects = [x for x in blob if isinstance(x, dict)]
        keyed = [x for x in objects if _pick_key(x, _TITLE_KEYS) and _pick_key(x, _URL_KEYS)]
        if len(keyed) >= MINIMUM_SELECTOR_FREQUENCY:
            found.append(keyed)
        for entry in blob:
            found.extend(_candidate_arrays(entry, depth + 1))
    elif isinstance(blob, dict):
        for value in blob.values():
            found.extend(_candidate_arrays(value, depth + 1))
    return found


def detect_json_state(dom: Element, **_: Any) -> list[dict[str, str]]:
    best: list[dict] = []
    for blob in _state_blobs(dom):
        for array in _candidate_arrays(blob):
            if len(array) > len(best):
                best = array
    return [
        _row(
            title=_pick_key(node, _TITLE_KEYS),
            url=_pick_key(node, _URL_KEYS),
            content=_pick_key(node, _CONTENT_KEYS),
            published_at=_pick_key(node, _DATE_KEYS),
            guid=_pick_key(node, _ID_KEYS),
        )
        for node in best
    ]


# ── detector: selector_frequency ────────────────────────────────────────────────────


def detect_selector_frequency(dom: Element, **_: Any) -> list[dict[str, str]]:
    """The most repeated link-bearing block signature on the page (§2.1).

    Signature is ``(tag, sorted classes)`` — the structural identity a templating engine
    repeats per item. Only blocks that contain a link AND some text are counted, the top
    :data:`USE_TOP_SELECTORS` signatures are considered, and a signature must repeat at least
    :data:`MINIMUM_SELECTOR_FREQUENCY` times. Ties break on document order, so the detector
    is deterministic on a page with two equally-frequent templates.
    """
    counts: dict[tuple[str, tuple[str, ...]], list[Element]] = {}
    order: list[tuple[str, tuple[str, ...]]] = []
    for el in dom.iter_descendants():
        if el.tag in ("html", "body", "head", "script", "style", "a", "nav", "footer", "header"):
            continue
        if not _first_link(el) or not el.text:
            continue
        sig = (el.tag, el.classes)
        if sig not in counts:
            counts[sig] = []
            order.append(sig)
        counts[sig].append(el)
    ranked = sorted(order, key=lambda s: (-len(counts[s]), order.index(s)))[:USE_TOP_SELECTORS]
    for sig in ranked:
        group = counts[sig]
        if len(group) < MINIMUM_SELECTOR_FREQUENCY:
            continue
        # Nested containers share their child's link, so a signature whose members contain
        # each other is a wrapper, not a row; keep only the outermost of any such chain.
        members = [el for el in group if not any(_is_ancestor(other, el) for other in group)]
        if len(members) >= MINIMUM_SELECTOR_FREQUENCY:
            return [_block_row(el) for el in members]
    return []


def _is_ancestor(maybe: Element, el: Element) -> bool:
    node = el.parent
    while node is not None:
        if node is maybe:
            return True
        node = node.parent
    return False


# ── declarative extraction (§2.2) ───────────────────────────────────────────────────

#: The extraction config's field names → the raw-row keys they populate. ``description`` maps
#: onto ``content`` because the shipped :class:`SourceItem` calls that field ``content``;
#: keeping html2rss's own name in the CONFIG (what a user writes) while the item keeps the
#: contract's name is the right way round — the config is the user-facing vocabulary.
_FIELD_TO_ROW = {
    "title": "title",
    "description": "content",
    "url": "url",
    "author": "author",
    "guid": "guid",
    "published_at": "published_at",
}


def _extract_field(scope: Element, config: dict, *, page_url: str, sanitize_default: bool) -> str:
    extractor = str(config.get("extractor") or "text")
    if extractor == "static":
        raw = str(config.get("value") or "")
    else:
        selector = str(config.get("selector") or "")
        target = select_one(scope, selector) if selector else scope
        if target is None:
            return ""
        if extractor == "text":
            raw = target.text
        elif extractor == "html":
            raw = target.inner_html
        elif extractor == "href":
            raw = target.attrs.get("href") or ""
        else:
            raw = target.attrs.get(str(config.get("attribute") or "").lower()) or ""
    steps = list(config.get("post_process") or [])
    names = {str(s.get("name") or "") for s in steps}
    if extractor == "html" and sanitize_default and "sanitize_html" not in names:
        # §2.2's sanitize_html default-ON: prepended, so a config's own chain still runs on
        # already-safe markup. A user who wants the raw bytes says sanitize_html: false at
        # the spec level, which is a visible decision rather than an omission.
        steps = [{"name": "sanitize_html"}, *steps]
    # Deliberately NO implicit `parse_uri` on an href here: :func:`apply_hygiene` resolves the
    # url field against the page for BOTH the manual and the auto paths, so adding it here as
    # well produced a branch no test could distinguish — a second resolution of an
    # already-absolute URL is a no-op. One resolution point, in the path both halves share.
    return apply_post_process(raw, steps, page_url=page_url)


def extract_declared(
    dom: Element, extraction: dict, *, page_url: str, sanitize_default: bool
) -> list[dict[str, str]]:
    """Run a user's ``spec.extraction`` config over the page (§2.2's escape hatch)."""
    items_cfg = extraction.get("items") or {}
    selector = str(items_cfg.get("selector") or "")
    if not selector:
        return []
    rows: list[dict[str, str]] = []
    for scope in select(dom, selector):
        row = _row()
        for field_name, row_key in _FIELD_TO_ROW.items():
            config = extraction.get(field_name)
            if isinstance(config, dict):
                row[row_key] = _extract_field(
                    scope, config, page_url=page_url, sanitize_default=sanitize_default
                ).strip()
        rows.append(row)
    return rows


# ── output hygiene (§2.2) ───────────────────────────────────────────────────────────


def _registrable(host: str) -> str:
    """The last two labels of a host — enough to treat ``blog.example.com`` and
    ``example.com`` as the same site without shipping a public-suffix list."""
    labels = (host or "").lower().strip(".").split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else (host or "").lower()


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def apply_hygiene(rows: list[dict], *, page_url: str, spec: dict) -> list[SourceItem]:
    """§2.2's output hygiene, then identity. The floors, in order:

    * relative links are resolved against the page (an unresolved link is not an identity);
    * ``keep_different_domain: drop`` (default) removes off-site links — ads, share widgets
      and "recommended for you" rails, which is most of what a naive detector picks up;
    * a title under ``min_words_title`` words is DISCARDED (not the item — §2.2 says a valid
      item needs title *or* description, so a two-word link with real body text survives
      titled by its URL, while bare navigation chrome has neither and is dropped);
    * an item with neither title nor description is dropped;
    * an item with no derivable guid is dropped, following ``feed_source``: the seen-set can
      only gate what it can name, so an un-keyable item would re-ingest on every poll.
    """
    from personalclaw.knowledge.source_identity import compose_guid

    keep_offsite = (
        str(spec.get("keep_different_domain") or KEEP_DIFFERENT_DOMAIN_DROP)
        == KEEP_DIFFERENT_DOMAIN_KEEP
    )
    min_words = int(spec.get("min_words_title", DEFAULT_MIN_WORDS_TITLE))
    page_host = _registrable(urlsplit(page_url).hostname or "")
    out: list[SourceItem] = []
    for row in rows:
        url = (row.get("url") or "").strip()
        if url:
            url = urljoin(page_url, url)
            if url.lower().startswith(("javascript:", "data:", "mailto:", "tel:")):
                url = ""
        if url and not keep_offsite:
            if _registrable(urlsplit(url).hostname or "") != page_host:
                continue
        title = (row.get("title") or "").strip()
        if title and _word_count(title) < min_words:
            title = ""
        content = (row.get("content") or "")[:MAX_ITEM_CHARS].strip()
        if not title and not content:
            continue
        published = (row.get("published_at") or "").strip()
        guid = compose_guid(
            guid=row.get("guid") or "", url=url, title=title, published_at=published
        )
        if not guid:
            continue
        metadata: dict[str, Any] = {}
        author = (row.get("author") or "").strip()
        if author:
            metadata["author"] = author
        out.append(
            SourceItem(
                guid=guid,
                title=title or url or guid,
                content=content,
                url=url,
                published_at=published,
                metadata=metadata,
            )
        )
    return out


def looks_like_js_shell(html: str, dom: Element) -> bool:
    """Whether this page is a client-rendered shell rather than a document (§2.3).

    Measured, not guessed: a page that ships script and has less visible text than
    :data:`JS_SHELL_MAX_TEXT_CHARS` built its content elsewhere. This is the discrimination
    that keeps "you pointed at a homepage" (plenty of text) from being reported with the same
    remediation as "this needs JavaScript" (almost none) — one is fixed by a different URL
    and the other by a budget knob, so collapsing them would send the user the wrong way.
    """
    if not html:
        return False
    has_script = any(node.tag == "script" for node in dom.iter_descendants())
    body = next((n for n in dom.iter_descendants() if n.tag == "body"), dom)
    return has_script and len(body.text) < JS_SHELL_MAX_TEXT_CHARS


# ── the provider ────────────────────────────────────────────────────────────────────


class WebSourceProvider(KnowledgeSourceProvider):
    """Poll-capable provider over a watched web page (§2).

    Spec keys are :data:`SPEC_SCHEMA`; ``budget`` (on the source row, not the spec) carries
    ``{max_requests, allow_render}``. ``fetch_fn``/``render_fn`` are the two injectable seams
    and the ONLY ways bytes enter — which is what keeps tier 1 under the engine's ``SOURCE``
    egress policy and tier 2 on the guard-pre-flighting core render path.
    """

    #: A page is more expensive to poll than a conditional-GET feed, and a changelog does not
    #: change every quarter hour. The engine still clamps this up to its own network floor.
    poll_interval_seconds = 3600

    def __init__(
        self,
        store: Any,
        *,
        fetch_fn: Callable[..., Any] | None = None,
        render_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._store = store
        self._fetch_fn = fetch_fn
        self._render_fn = render_fn

    @property
    def name(self) -> str:
        return "watched-page"

    @property
    def display_name(self) -> str:
        return "Watched Page"

    # ── corpus contract (the library itself owns search/get) ─────────────────────────

    async def list_sources(self) -> list[KnowledgeSource]:
        return [
            KnowledgeSource(id=s["id"], name=s["name"], source_type="web_page", provider=self.name)
            for s in self._store.list_sources()
            if s.get("provider") == self.name
        ]

    async def search(self, query: str, limit: int = 10) -> list[KnowledgeItem]:
        # Polled items live in the library, so the library's own search covers them; a second
        # search path here would be a divergent ranking of the same rows.
        return []

    async def get_item(self, item_id: str) -> KnowledgeItem | None:
        return None

    # ── spec validation (save time AND poll time) ───────────────────────────────────

    def validate_spec(self, spec: dict) -> tuple[bool, str]:
        """Validate a web-source spec against :data:`SPEC_SCHEMA`. Fail-CLOSED.

        Run at the top of every :meth:`poll` as well as on save, for the reason ``dir_source``
        documents: a spec is a mutable row an MCP tool or a hand-edit can change after the
        fact, so a save-only guard is one edit away from being bypassed. Here the stakes are
        a URL — an unvalidated spec is an arbitrary fetch target on a timer.
        """
        err = _validate_against(spec or {}, SPEC_SCHEMA, "spec")
        return (False, err) if err else (True, "")

    # ── fetch seams ─────────────────────────────────────────────────────────────────

    async def _fetch(self, url: str, *, policy: Any, headers: dict[str, str]) -> Any:
        """Tier 1. Defaults to the guarded ``net.fetch``; ``fetch_fn`` replaces it in tests so
        no test opens a socket."""
        if self._fetch_fn is not None:
            return await self._fetch_fn(url, policy=policy, headers=headers)
        from personalclaw.net.client import fetch

        return await fetch(url, policy=policy, headers=headers)

    async def _render(self, url: str, *, policy: Any) -> Any:
        """Tier 2 — core's ``web/render.py`` and nothing else (§2.3). It pre-flights the same
        egress guard because a headless browser does its own DNS and would otherwise bypass
        ``net.fetch``'s IP pinning."""
        if self._render_fn is not None:
            return await self._render_fn(url, policy=policy)
        from personalclaw.web.render import render_url

        return await render_url(url, policy=policy)

    # ── the detector stack ──────────────────────────────────────────────────────────

    def _enabled_detectors(self, spec: dict) -> tuple[str, ...]:
        """The detectors to run, in :data:`DETECTOR_ORDER`. A spec's list is a FILTER over the
        stack, not a re-ordering: reliability order is the provider's decision, and a config
        that could put ``selector_frequency`` first would let a heuristic outrank a
        declaration on that source forever."""
        wanted = spec.get("detectors")
        if not isinstance(wanted, list) or not wanted:
            return DETECTOR_ORDER
        chosen = {str(d) for d in wanted}
        return tuple(d for d in DETECTOR_ORDER if d in chosen)

    async def _run_detector(
        self, name: str, dom: Element, *, page_url: str, budget: _Budget, policy: Any
    ) -> list[dict[str, str]]:
        if name == DETECTOR_WORDPRESS_API:
            return await self._detect_wordpress_api(
                dom, page_url=page_url, budget=budget, policy=policy
            )
        if name == DETECTOR_JSON_LD:
            return detect_json_ld(dom)
        if name == DETECTOR_SEMANTIC_HTML:
            return detect_semantic_html(dom)
        if name == DETECTOR_JSON_STATE:
            return detect_json_state(dom)
        if name == DETECTOR_SELECTOR_FREQUENCY:
            return detect_selector_frequency(dom)
        # Unreachable: the spec's detector list is enum-validated and the stack is a constant.
        raise AssertionError(f"unknown detector {name!r}")

    async def _detect_wordpress_api(
        self, dom: Element, *, page_url: str, budget: _Budget, policy: Any
    ) -> list[dict[str, str]]:
        """WordPress REST posts, when the page advertises the endpoint (§2.1).

        Costs one request from the poll's shared budget, and only when the ``api.w.org`` link
        is actually present — so a non-WordPress page pays nothing for having this detector
        first in the stack.
        """
        base = ""
        for node in dom.iter_descendants():
            if node.tag != "link":
                continue
            rel = (node.attrs.get("rel") or "").strip().lower()
            if rel == "https://api.w.org/" and node.attrs.get("href"):
                base = urljoin(page_url, node.attrs["href"])
                break
        if not base or not budget.take():
            return []
        endpoint = base.rstrip("/") + "/wp/v2/posts?per_page=20"
        try:
            resp = await self._fetch(
                endpoint, policy=policy, headers={"Accept": "application/json"}
            )
            posts = json.loads(getattr(resp, "text", "") or "")
        except Exception:  # noqa: BLE001 — a missing/odd REST endpoint just means no items
            logger.debug("wordpress_api: %s unusable", endpoint, exc_info=True)
            return []
        if not isinstance(posts, list):
            return []
        rows: list[dict[str, str]] = []
        for post in posts:
            if not isinstance(post, dict):
                continue
            rows.append(
                _row(
                    title=_rendered_title(post.get("title")),
                    url=str(post.get("link") or ""),
                    content=_rendered(post.get("excerpt")) or _rendered(post.get("content")),
                    published_at=str(post.get("date_gmt") or post.get("date") or ""),
                    guid=str(post.get("id") or "") or _rendered(post.get("guid")),
                )
            )
        return rows

    async def _detect(
        self, dom: Element, *, page_url: str, spec: dict, budget: _Budget, policy: Any
    ) -> tuple[str, list[SourceItem]]:
        """Run the stack (or the manual config) and return the winner plus its items.

        Hygiene runs INSIDE the loop: a detector whose candidates all fail the §2.2 floors
        found nothing, so the stack continues. Deciding a winner on raw candidates instead is
        how a nav menu wins over the article list further down the page.
        """
        sanitize_on = spec.get("sanitize_html", True) is not False
        extraction = spec.get("extraction")
        if isinstance(extraction, dict) and extraction:
            # A manual config REPLACES the stack rather than joining it (§2.2 is the escape
            # hatch for when all five detectors miss); falling back to auto here would hide a
            # broken selector behind whatever the frequency detector happens to find.
            rows = extract_declared(
                dom, extraction, page_url=page_url, sanitize_default=sanitize_on
            )
            items = apply_hygiene(rows, page_url=page_url, spec=spec)
            for item in items:
                item.metadata["detector"] = DETECTOR_MANUAL
            return DETECTOR_MANUAL, items
        for name in self._enabled_detectors(spec):
            rows = await self._run_detector(
                name, dom, page_url=page_url, budget=budget, policy=policy
            )
            items = apply_hygiene(rows, page_url=page_url, spec=spec)
            if items:
                for item in items:
                    item.metadata["detector"] = name
                return name, items
        return "", []

    # ── the escalating collect (§2.3) ───────────────────────────────────────────────

    async def _collect(
        self,
        *,
        url: str,
        spec: dict,
        budget: _Budget,
        policy: Any,
        validators: dict[str, str],
        allow_render: bool,
    ) -> _Collected:
        """Fetch, detect, and escalate to the render tier when the OUTCOME demands it.

        Every request in here — tier 1, the WordPress sub-request inside the stack, the render
        — comes out of the one ``budget``, which is what makes §2.3's "all attempts in one
        poll draw on a single max_requests" true rather than aspirational.
        """
        if not budget.take():
            return _Collected(
                error=f"per-poll request budget spent ({budget.max_requests} requests)"
            )
        try:
            resp = await self._fetch(
                url, policy=policy, headers=conditional_get.conditional_headers(validators)
            )
        except Exception as exc:  # noqa: BLE001 — egress denial, timeout, DNS: all soft
            return _Collected(error=f"fetch failed: {exc}"[:200])
        status = int(getattr(resp, "status", 0) or 0)
        if status == 304:
            return _Collected(not_modified=True, cursor_state=dict(validators))
        if status >= 400 or status == 0:
            return _Collected(error=f"page returned HTTP {status}")
        html = getattr(resp, "text", "") or ""
        cursor_state = conditional_get.validators_from(getattr(resp, "headers", None))
        dom = parse_html(html)
        detector, items = await self._detect(
            dom, page_url=url, spec=spec, budget=budget, policy=policy
        )
        if items:
            return _Collected(items=items, detector=detector, cursor_state=cursor_state)

        if not looks_like_js_shell(html, dom):
            # A page that rendered plenty of text and still has no items is the wrong URL, not
            # a rendering problem (§2.1) — so it gets the listing-page guidance and NO render
            # attempt, because a browser would find exactly the same nothing more expensively.
            return _Collected(
                detector=detector, guidance=LISTING_PAGE_GUIDANCE, cursor_state=cursor_state
            )
        if not allow_render:
            # The one clause SC#2 names: needed the tier, not allowed it. A distinct health
            # status, not a generic degraded, because the remediation is one knob.
            return _Collected(
                escalations=["render tier needed but budget.allow_render is false"],
                guidance=RENDER_TIER_GUIDANCE,
                health_status=HEALTH_NEEDS_RENDER,
                detector=detector,
                cursor_state=cursor_state,
            )
        if not budget.take():
            return _Collected(
                escalations=[
                    "render escalation refused: per-poll request budget spent "
                    f"({budget.max_requests} requests)"
                ],
                guidance=RENDER_TIER_GUIDANCE,
                detector=detector,
                cursor_state=cursor_state,
            )
        try:
            rendered = await self._render(url, policy=policy)
        except Exception as exc:  # noqa: BLE001 — a render crash is a reason, not a dead loop
            return _Collected(
                escalations=[f"render tier raised: {type(exc).__name__}: {exc}"[:200]],
                guidance=RENDER_TIER_GUIDANCE,
                health_status=HEALTH_NEEDS_RENDER,
                detector=detector,
                cursor_state=cursor_state,
            )
        if getattr(rendered, "unavailable", False):
            # Allowed but not installed. STILL `needs render tier`: the page does need it, and
            # the remediation is an install rather than a knob.
            return _Collected(
                escalations=["render tier unavailable; install personalclaw[js-render]"],
                guidance=str(getattr(rendered, "error", "") or RENDER_TIER_GUIDANCE),
                health_status=HEALTH_NEEDS_RENDER,
                detector=detector,
                cursor_state=cursor_state,
            )
        if not getattr(rendered, "ok", False):
            reason = getattr(rendered, "error", "") or "unknown"
            return _Collected(
                escalations=[f"render failed: {reason}"[:200]],
                guidance=RENDER_TIER_GUIDANCE,
                detector=detector,
                cursor_state=cursor_state,
            )
        rendered_dom = parse_html(str(getattr(rendered, "html", "") or ""))
        detector, items = await self._detect(
            rendered_dom, page_url=url, spec=spec, budget=budget, policy=policy
        )
        return _Collected(
            items=items,
            detector=detector,
            escalations=[
                (
                    f"escalated to render tier; extracted {len(items)} item(s)"
                    if items
                    else "escalated to render tier; still no items after JS render"
                )
            ],
            guidance="" if items else LISTING_PAGE_GUIDANCE,
            cursor_state=cursor_state,
        )

    # ── preview (§2.4) ──────────────────────────────────────────────────────────────

    async def preview(
        self, spec: dict, *, budget: dict | None = None, policy: Any = None
    ) -> SourcePreview:
        """Dry-run the extraction for the paste-URL create flow (§2.4).

        Persists nothing — no item, no cursor, no seen-set row — and takes the SPEC rather
        than a ``source_id``, because the whole point is to run before a source exists. The
        request budget is still enforced: a preview is a real fetch at somebody else's server,
        and a tuning loop that ignored the budget is the one part of this flow that could
        become abusive.

        Declared on this provider rather than on
        :class:`~personalclaw.knowledge_providers.base.KnowledgeSourceProvider`: a feed's or a
        directory's "preview" is just its poll, and only the web kind has a detect-then-tune
        loop to preview. Putting it on the ABC would have made it an abstract method the two
        shipped providers must satisfy with a stub — a dead surface on both.
        """
        ok, err = self.validate_spec(spec or {})
        if not ok:
            return SourcePreview(error=err)
        counter = _Budget(max_requests=_max_requests(budget))
        got = await self._collect(
            url=str((spec or {})["url"]),
            spec=dict(spec or {}),
            budget=counter,
            policy=policy,
            validators={},
            allow_render=_allow_render(budget),
        )
        return SourcePreview(
            items=got.items[: _item_cap(spec or {})],
            detector=got.detector,
            escalations=got.escalations,
            requests_used=counter.used,
            guidance=got.guidance,
            health_status=got.health_status,
            error=got.error,
        )

    # ── the poll ────────────────────────────────────────────────────────────────────

    async def poll(
        self, source_id: str, cursor: str = "", *, policy: Any = None
    ) -> SourcePollResult:
        """One escalating fetch + detect. Never raises to the engine (§1.1).

        The cursor carries the conditional-GET validators, so a page that has not changed
        answers 304 and the poll costs a few hundred bytes. ``budget`` is read from the SOURCE
        row rather than the spec: it is an operational allowance the user tunes independently
        of what the page is or how it is parsed.
        """
        source = self._store.get_source(source_id)
        if source is None:
            return SourcePollResult(error=f"source {source_id} no longer exists")
        spec = dict(source.get("spec") or {})
        ok, err = self.validate_spec(spec)
        if not ok:
            return SourcePollResult(cursor=cursor, error=err)
        budget_cfg = source.get("budget") or {}
        validators = conditional_get.parse_validators(cursor)
        counter = _Budget(max_requests=_max_requests(budget_cfg))
        got = await self._collect(
            url=str(spec["url"]),
            spec=spec,
            budget=counter,
            policy=policy,
            validators=validators,
            allow_render=_allow_render(budget_cfg),
        )
        if got.not_modified:
            # Cursor returned verbatim so the SAME validators are offered next time; a 304 that
            # dropped them would turn every subsequent poll into a full download.
            return SourcePollResult(items=[], cursor=cursor)
        if got.error:
            return SourcePollResult(
                cursor=cursor,
                error=got.error,
                escalations=got.escalations,
                health_status=got.health_status,
            )
        new_cursor = conditional_get.encode(got.cursor_state) if got.cursor_state else cursor
        if not got.items:
            # An empty extraction is reported as a soft error carrying the REMEDIATION, so a
            # source that found nothing is visibly actionable rather than quietly healthy.
            return SourcePollResult(
                cursor=new_cursor,
                error=got.guidance,
                escalations=got.escalations,
                health_status=got.health_status,
            )
        return SourcePollResult(
            items=got.items[: _item_cap(spec)],
            cursor=new_cursor,
            escalations=got.escalations,
        )


def _rendered(value: Any) -> str:
    """A WordPress REST field, which is either a string or ``{"rendered": "..."}``."""
    if isinstance(value, dict):
        return str(value.get("rendered") or "").strip()
    return str(value or "").strip()


def _rendered_title(value: Any) -> str:
    """A WordPress REST ``title.rendered``, which is HTML-ESCAPED text.

    Found by driving the create flow against a real WordPress listing page (WS-9): every
    apostrophe arrived as ``&#8217;`` — ``Don&#8217;t stop early`` — because WP escapes these
    fields and nothing decoded them. A title is plain text by definition and is rendered as
    text on every surface, so leaving the entities in means they show up literally in the
    preview, in the item row and in the library.

    Scoped to the TITLE deliberately. ``content`` is markup, where the same escaping is
    meaningful (an escaped ``&lt;script&gt;`` in a post's body is *shown code*), and both of
    its readers already convert through the html→text seam. Unescaping it here would decode
    that back into live markup before ``sanitize_html`` ever sees it.
    """
    return unescape(_rendered(value))


def _max_requests(budget: dict | None) -> int:
    """The poll's request allowance. ``or`` would swallow an explicit 0 as "unset" — the
    defaulted-field-hides-an-invalid-input shape — so presence is checked explicitly."""
    raw = (budget or {}).get("max_requests")
    if raw in (None, ""):
        return DEFAULT_MAX_REQUESTS
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_MAX_REQUESTS


def _allow_render(budget: dict | None) -> bool:
    """§2.3's opt-in. Default FALSE, and only a literal ``True`` turns it on — a truthy
    string from a hand-edited row must not silently license a browser launch."""
    return (budget or {}).get("allow_render") is True


def _item_cap(spec: dict) -> int:
    raw = (spec or {}).get("max_items")
    if raw in (None, ""):
        return MAX_ITEMS_PER_POLL
    try:
        return min(max(1, int(raw)), MAX_ITEMS_PER_POLL)
    except (TypeError, ValueError):
        return MAX_ITEMS_PER_POLL
