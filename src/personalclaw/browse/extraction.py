"""Text-only STRUCTURAL page extraction — the token-frugal perception layer for autonomous
browse (BA-1, plan §1 + §2).

``web/extract.py`` is READABILITY extraction: it returns the page's *prose* (trafilatura
main-content) for reading. That is the wrong shape for an agent that has to *act* on a page —
it needs the clickable/typeable surface (links, forms, buttons) with a way to name each one.
This module is the complement: it walks the DOM and emits a compact representation —

  * a main-text body (≤4000 chars), reusing the connector's chrome-stripping ``html_to_text``
    seam so we do not fork a second text pipeline;
  * a Links DSL (§1.2) — navigable links, filtered + deduped;
  * a Forms DSL (§1.3) — every form field with its type/required hints;

and it assigns each interactive element a **stable ``ElementRef``**. The ref is derived from
durable identity (``sha1(role + accessible_name + form_id)``, plan amendment 2026-07-26(a)),
NOT a positional counter — so a re-snapshot after an *unrelated* DOM mutation keeps unchanged
elements' refs, and an agent's "CLICK <ref>" still names the same button after the page
updates elsewhere. Positional indexes hit a TOCTOU every dynamic page defeats; identity refs
do not.

Why structural parsing runs on stdlib ``HTMLParser`` over the *raw* markup, not ``nh3``: nh3's
sanitizer allowlist is prose-oriented and DROPS ``<form>``/``<input>``/``<button>``/``<select>``
outright, which are exactly the elements we must extract. We never render this markup — we read
specific attributes + short label text off named tags — and the text body still flows through
the sanitizing ``html_to_text`` (scripts/styles stripped, images → ``[IMAGE: alt]`` placeholders
so no ``data:`` base64 blob can ride into context). The browse LOOP (BA-3) fences the whole
representation via ``fence_untrusted`` before it reaches a model; that is the trust boundary,
not this pure function.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from personalclaw.browse.credentials import WITHHELD, is_credential_input, screen_url
from personalclaw.knowledge.connectors.base import html_to_text

#: §1.1 hard cap — ~800-1000 tokens of prose. The compression layer (compress.py) tightens
#: this further to fit the whole representation under a token budget; this is the text-body
#: ceiling on its own.
MAX_TEXT_CHARS = 4000

#: §1.1 — the Links section is top-N, deduped. A page can carry thousands of links (nav,
#: footers, related-posts grids); an agent needs the salient handful, not the sitemap.
MAX_LINKS = 50

# ── Interactive-element roles (plain strings, not an Enum: an Enum member nobody writes
# trips the inert-surface census, and these are matched textually anyway). ──
ROLE_LINK = "link"
ROLE_BUTTON = "button"
ROLE_FIELD = "field"
ROLE_CHECKBOX = "checkbox"
ROLE_SELECT = "select"

# Link filtering (§1.2): drop asset/font/manifest targets — they are never "navigate here".
_ASSET_EXT = {
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "svg",
    "ico",
    "bmp",
    "tiff",
    "avif",
    "css",
    "js",
    "map",
    "woff",
    "woff2",
    "ttf",
    "eot",
    "otf",
    "webmanifest",
}
# §1.2: strip tracking query params, keep only the ones that carry real navigation intent.
_KEEP_PARAMS = {"q", "s", "search", "page"}
# Schemes that are not a navigable page (javascript no-ops, base64 blobs, contact links).
_NON_NAV_SCHEMES = ("javascript:", "data:", "blob:", "mailto:", "tel:", "vbscript:")

_IMG_RE = re.compile(r"<img\b[^>]*?>", re.IGNORECASE | re.DOTALL)
_ALT_RE = re.compile(r'\balt=["\']([^"\']*)["\']', re.IGNORECASE)
_WS_RE = re.compile(r"[ \t]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class ElementRef:
    """A stable handle to one interactive DOM element (plan amendment 2026-07-26(a)).

    ``ref`` is ``sha1(role + accessible_name + form_id)[:8]`` — stable across re-snapshots
    because it depends only on the element's own identity, never on its position. Colliding
    identities (same role+label+form) are disambiguated by an occurrence ordinal folded into
    the hash input, so refs stay unique without becoming positional.
    """

    ref: str
    role: str  # ROLE_LINK | ROLE_BUTTON | ROLE_FIELD | ROLE_CHECKBOX | ROLE_SELECT
    label: str
    state: str = ""  # field value, checkbox "checked"/"unchecked", or button caption
    target: str = ""  # link href (role == ROLE_LINK); "" otherwise
    form: str = ""  # owning form name/id ("" when not inside a <form>)
    note: str = ""  # rendered attribute hint for fields, e.g. 'type=email required'
    #: BA-4: this field holds a credential, so ``state`` is :data:`~browse.credentials.WITHHELD`
    #: rather than the value — and the loop REFUSES to type into it. Carried on the ref rather
    #: than re-derived at each consumer because the consumers are a renderer, an executor and an
    #: audit row: three places re-deciding "is this a password" is three places to disagree.
    credential: bool = False


@dataclass(frozen=True)
class FormRepr:
    """One ``<form>`` (or the implicit group of form-less fields, name="")."""

    name: str
    fields: tuple[ElementRef, ...]


@dataclass(frozen=True)
class PageExtraction:
    """The structured representation of a page: text body + Links DSL + Forms DSL sources."""

    url: str
    text: str
    links: tuple[ElementRef, ...]
    forms: tuple[FormRepr, ...]


# ── Raw records the parser builds before refs are assigned ──


@dataclass
class _LinkRec:
    href: str
    text: str = ""
    aria: str = ""


@dataclass
class _FieldRec:
    role: str
    label: str
    state: str = ""
    note: str = ""
    text: str = ""  # accumulated inner text for <button>/<textarea>
    credential: bool = False  # BA-4 — see `_handle_input`


@dataclass
class _FormRec:
    name: str
    fields: list[_FieldRec] = field(default_factory=list)


class _StructureParser(HTMLParser):
    """Walk the DOM once, collecting links and form fields with their owning form.

    Content inside ``<script>``/``<style>``/``<noscript>``/``<template>`` is skipped so page
    JS never leaks in as a label. ``<a>``/``<button>``/``<textarea>`` accumulate their inner
    text (the accessible name / default value) between start and end tags.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[_LinkRec] = []
        self.forms: list[_FormRec] = []
        self._form_stack: list[_FormRec] = []
        self._default_form: _FormRec | None = None
        self._capture: list[_LinkRec | _FieldRec] = []
        self._skip_depth = 0

    def _current_form(self) -> _FormRec:
        if self._form_stack:
            return self._form_stack[-1]
        # Form-less fields (a bare <button>, an <input> outside any <form>) group under an
        # implicit form so they still get refs and appear in the Forms DSL.
        if self._default_form is None:
            self._default_form = _FormRec(name="")
            self.forms.append(self._default_form)
        return self._default_form

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript", "template"):
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "form":
            name = a.get("name") or a.get("id") or ""
            rec = _FormRec(name=name)
            self.forms.append(rec)
            self._form_stack.append(rec)
        elif tag == "a":
            link = _LinkRec(href=a.get("href", ""), aria=a.get("aria-label") or a.get("title", ""))
            self.links.append(link)
            self._capture.append(link)
        elif tag == "button":
            fr = _FieldRec(role=ROLE_BUTTON, label="", state="")
            self._current_form().fields.append(fr)
            self._capture.append(fr)
        elif tag == "input":
            self._handle_input(a)
        elif tag == "textarea":
            fr = _FieldRec(role=ROLE_FIELD, label=(a.get("name") or a.get("id") or "text"))
            if "required" in a:
                fr.note = "textarea required"
            else:
                fr.note = "textarea"
            # A <textarea> has no `type`, so only the name/autocomplete signals apply — but a
            # "paste your API token here" box IS a textarea on a great many admin pages, and the
            # invariant does not care which tag the secret arrived in.
            fr.credential = is_credential_input(
                "textarea",
                name=a.get("name") or a.get("id") or "",
                autocomplete=a.get("autocomplete") or "",
                placeholder=a.get("placeholder") or "",
            )
            if fr.credential:
                fr.state = WITHHELD
            self._current_form().fields.append(fr)
            self._capture.append(fr)
        elif tag == "select":
            label = a.get("name") or a.get("id") or "select"
            note = "select" + (" required" if "required" in a else "")
            self._current_form().fields.append(_FieldRec(role=ROLE_SELECT, label=label, note=note))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # XHTML self-closing (<input/>, <a/>): route through start; the matching end is a
        # no-op for void elements since they were never pushed onto the capture stack.
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "template"):
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "form":
            if self._form_stack:
                self._form_stack.pop()
        elif tag in ("a", "button", "textarea"):
            if self._capture:
                self._capture.pop()

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._capture:
            return
        top = self._capture[-1]
        top.text += data

    def _handle_input(self, a: dict[str, str]) -> None:
        itype = (a.get("type") or "text").lower()
        if itype == "hidden":
            return  # never a surface the agent acts on
        if itype in ("submit", "button", "image", "reset"):
            label = a.get("value") or a.get("name") or ("submit" if itype == "submit" else "button")
            self._current_form().fields.append(
                _FieldRec(role=ROLE_BUTTON, label=label, state=label)
            )
            return
        if itype in ("checkbox", "radio"):
            label = a.get("name") or a.get("id") or a.get("value") or itype
            state = "checked" if "checked" in a else "unchecked"
            self._current_form().fields.append(
                _FieldRec(role=ROLE_CHECKBOX, label=label, state=state, note=f"type={itype}")
            )
            return
        label = a.get("name") or a.get("id") or a.get("placeholder") or itype
        extras: list[str] = []
        if itype != "text":
            extras.append(f"type={itype}")
        if "required" in a:
            extras.append("required")
        if a.get("placeholder"):
            extras.append(f'placeholder="{a["placeholder"]}"')
        # 🔴 BA-4's never-transits invariant, at the ONE point a DOM value becomes browse state.
        #
        # For a credential input the `value` attribute is NOT READ — not read and then redacted,
        # not read and then dropped: never read. So no representation of it exists anywhere
        # downstream, which is what makes the invariant structural instead of a habit. Every
        # model-visible string in this feature derives from the records built here, so a value
        # that never enters cannot leave through the outline, the Links/Forms DSL, a note, a park
        # sentence, a SEL row or a log line — and there is no ordering to get right and no second
        # redaction pass to garble a composed `field: value` line.
        #
        # The state is WITHHELD unconditionally, not "WITHHELD if non-empty". Whether the browser
        # has a password prefilled is itself a fact about the user's credential store, and a state
        # that reads `("")` on an empty box and `[withheld]` on a full one leaks exactly that.
        credential = is_credential_input(
            itype,
            name=a.get("name") or a.get("id") or "",
            autocomplete=a.get("autocomplete") or "",
            placeholder=a.get("placeholder") or "",
        )
        self._current_form().fields.append(
            _FieldRec(
                role=ROLE_FIELD,
                label=label,
                state=WITHHELD if credential else a.get("value", ""),
                note=" ".join(extras),
                credential=credential,
            )
        )


def _make_ref(role: str, name: str, form_id: str, ordinal: int = 0) -> str:
    """``sha1(role + accessible_name + form_id)[:8]`` — the stable identity ref.

    The ordinal is folded in ONLY to break a genuine collision (two elements with identical
    role+label+form); it is not a position, so an unrelated element added elsewhere never
    perturbs an existing ref."""
    ident = f"{role}\x1f{name}\x1f{form_id}"
    if ordinal:
        ident = f"{ident}\x1f{ordinal}"
    return hashlib.sha1(ident.encode("utf-8")).hexdigest()[:8]


def _clean_link(href: str) -> str | None:
    """Apply the §1.2 link filter; return the cleaned URL or None if it is not navigable."""
    href = href.strip()
    if not href or href.startswith("#"):
        return None  # empty or fragment-only anchor
    low = href.lower()
    if low.startswith(_NON_NAV_SCHEMES):
        return None  # javascript:/data:/mailto: etc. — never a page to navigate to
    if len(href) > 100:
        return None  # measured: URLs this long are almost always tracking junk (§1.2)
    parts = urlsplit(href)
    last_seg = parts.path.rsplit("/", 1)[-1]
    ext = last_seg.rsplit(".", 1)[-1].lower() if "." in last_seg else ""
    if ext in _ASSET_EXT:
        return None
    if parts.query:
        kept = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() in _KEEP_PARAMS
        ]
        parts = parts._replace(query=urlencode(kept))
    return urlunsplit(parts)


def _image_alt(tag: str) -> str:
    m = _ALT_RE.search(tag)
    return m.group(1).strip() if m else ""


def _strip_images(html: str) -> str:
    """Replace every ``<img>`` with an ``[IMAGE: alt]`` placeholder BEFORE text conversion.

    html2text would otherwise render an image as ``![alt](src)`` — and a ``data:`` base64 src
    would then ride straight into the text body. Stripping to a placeholder (§1.1) is what
    keeps base64 out of the extracted prose."""

    def repl(m: re.Match[str]) -> str:
        alt = _image_alt(m.group(0))
        return f"[IMAGE: {alt}]" if alt else "[IMAGE]"

    return _IMG_RE.sub(repl, html)


def _text_body(html: str) -> str:
    """The ≤4000-char main text, reusing the connector's chrome-stripping ``html_to_text``."""
    text = html_to_text(_strip_images(html))
    text = _WS_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n\n", text).strip()
    return text[:MAX_TEXT_CHARS]


def _build_links(recs: list[_LinkRec]) -> list[tuple[str, str, str]]:
    """(label, cleaned_href) tuples, filtered + deduped + capped to MAX_LINKS.

    Returns triples carrying a raw identity key first so ref assignment can dedup on it."""
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for rec in recs:
        cleaned = _clean_link(rec.href)
        if cleaned is None:
            continue
        label = _WS_RE.sub(" ", rec.text).strip() or rec.aria.strip() or cleaned
        key = (label, cleaned)
        if key in seen:
            continue
        seen.add(key)
        out.append((label, cleaned, label))
        if len(out) >= MAX_LINKS:
            break
    return out


def extract_page(html: str, *, url: str = "") -> PageExtraction:
    """Parse HTML into a structured, ref-stable page representation (text + links + forms)."""
    if not html:
        return PageExtraction(url=url, text="", links=(), forms=())

    parser = _StructureParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # A malformed page must never hard-fail extraction — degrade to whatever was parsed.
        pass

    # Assign refs with a shared ordinal counter per identity, so a link and a field that
    # happen to hash the same identity still get distinct refs.
    ordinals: dict[tuple[str, str, str], int] = {}

    def next_ref(role: str, name: str, form_id: str) -> str:
        key = (role, name, form_id)
        n = ordinals.get(key, 0)
        ordinals[key] = n + 1
        return _make_ref(role, name, form_id, n)

    links: list[ElementRef] = []
    for label, cleaned, name in _build_links(parser.links):
        links.append(
            ElementRef(
                ref=next_ref(ROLE_LINK, name, ""),
                role=ROLE_LINK,
                label=label,
                target=cleaned,
            )
        )

    forms: list[FormRepr] = []
    for form_rec in parser.forms:
        fields: list[ElementRef] = []
        for fr in form_rec.fields:
            label = fr.label
            state = fr.state
            if fr.role in (ROLE_BUTTON,) and fr.text.strip():
                label = _WS_RE.sub(" ", fr.text).strip()
                state = label
            elif fr.role == ROLE_FIELD and fr.text.strip() and not state and not fr.credential:
                # `not fr.credential`: a <textarea> cannot be `type=password`, but it CAN be named
                # `api_token`, and folding its inner text in here would reinstate the value
                # `_handle_input` deliberately refused to read.
                state = _WS_RE.sub(" ", fr.text).strip()  # <textarea> default value
            fields.append(
                ElementRef(
                    ref=next_ref(fr.role, label, form_rec.name),
                    role=fr.role,
                    label=label,
                    state=state,
                    form=form_rec.name,
                    note=fr.note,
                    credential=fr.credential,
                )
            )
        if fields:
            forms.append(FormRepr(name=form_rec.name, fields=tuple(fields)))

    return PageExtraction(
        url=url,
        text=_text_body(html),
        links=tuple(links),
        forms=tuple(forms),
    )


def _field_state_repr(e: ElementRef) -> str:
    if e.role == ROLE_CHECKBOX:
        return f"({e.state or 'unchecked'})"
    return f'("{e.state}")'


def render_links_dsl(links: tuple[ElementRef, ...] | list[ElementRef]) -> str:
    """The §1.2 Links DSL — each link a ref-addressable line ``[ref] label → target``.

    The target is SCREENED here (BA-4) rather than on the :class:`ElementRef`, and the split is
    deliberate. ``page.CdpPageDriver``'s locator prelude finds a link by
    ``el.getAttribute("href") === TARGET`` first, so screening the stored target would demote every
    click to the label-only fallback and pick the wrong element on a page with two identically
    labelled links. So the ref keeps the real href for the DRIVER, and the value is removed at the
    one boundary where it would reach the model. ``CLICK <ref>`` still works on a screened link
    because clicking dispatches on the DOM element, never on the URL — which is why a token in a
    link is a privacy problem here and not a functional one.

    **What this screen actually covers, measured rather than assumed.** ``_clean_link``'s
    ``_KEEP_PARAMS`` is an ALLOWLIST (``q``/``s``/``search``/``page``), so no credential can survive
    in a link's QUERY — that half was already closed before BA-4 and this screen is redundant there.
    It is the FRAGMENT that ``_clean_link`` passes through untouched, and the fragment is precisely
    where the OAuth *implicit* flow returns ``#access_token=…``. Driven before keeping the call:
    ``/r#access_token=TOK123`` reached the rendered target verbatim. So this is one live screen over
    one real gap, not a second line of defence over a closed one.
    """
    if not links:
        return ""
    lines = ["## Links"]
    for e in links:
        lines.append(f"[{e.ref}] {e.label} → {screen_url(e.target)}")
    return "\n".join(lines)


def render_forms_dsl(forms: tuple[FormRepr, ...] | list[FormRepr]) -> str:
    """The §1.3 Forms DSL — one block per form, each field a ref-addressable line."""
    if not forms:
        return ""
    lines = ["## Forms"]
    for form in forms:
        lines.append(f'[form: "{form.name}"]')
        for e in form.fields:
            note = f" {e.note}" if e.note else ""
            lines.append(f"  [{e.ref}] {e.label} {_field_state_repr(e)}{note}")
    return "\n".join(lines)
