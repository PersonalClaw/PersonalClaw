"""While a browse step holds a stored secret's value, it names only the sites it was set to reach.

Its prompts, its links and the words a page wrote for them and its controls, the lines its steps
record, its notes, its parks, its live view and its audit rows show each address as the origin
``scheme://host[:port]/`` of a site the step knew before it touched the browser: its start page's,
and each host its egress allow-list names exactly (not a subdomain of one). An address with no host
shows by its scheme, when that is about, blob, data, file or javascript, and every other address,
host, port and scheme is ``[withheld]`` whole. A refusal is said in fixed words. Each is masked of
the value before anything keeps, relays or cuts it. A park that hands the tab to a person leaves
the page for them; any other end leaves the tab blank.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import types
import unicodedata
from dataclasses import asdict
from typing import Any
from urllib.parse import quote

import pytest
from test_browse_loop_and_provider import _Decide, _FakePage, _FakeSession

from personalclaw import filled_secrets
from personalclaw.browse import credentials, sentinels
from personalclaw.browse.loop import BrowseLoopResult, run_browse_loop

pytestmark = pytest.mark.security

#: A value with reserved characters, so each way an address can carry it differs.
VALUE = "Kx7q/Qp2w&Zm9e=Wd4r"
SITE = "https://orchard.example.test/"
START = SITE + "docs"
#: Markers a path, a query or a fragment carries; none may reach anything browse shows.
MARKS = ("PATHMARK", "QMARK", "FRAGM", "WQXZT", "PLMKV", "ABSPATH")


def _forms() -> list[str]:
    """The value as written, encoded once or twice, in base64, and its pieces."""
    once = quote(VALUE, safe="")
    whole = {VALUE, once, quote(once, safe=""), base64.b64encode(VALUE.encode()).decode()}
    return sorted(whole | {p for p in re.split(r"[^A-Za-z0-9]+", VALUE) if len(p) >= 4})


def _found(*texts: Any) -> list[str]:
    """Which form of the value, or which marker, any of *texts* holds, in any case."""
    blob = "\n".join(
        t if isinstance(t, str) else json.dumps(t, default=str, ensure_ascii=False) for t in texts
    )
    return [f for f in (*_forms(), *MARKS) if f.lower() in blob.lower()]


class _Rows:
    """The audit rows the loop and the session write, as written."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from personalclaw import sel as sel_mod
        from personalclaw.browse import cdp

        self.rows: list[Any] = []
        log = types.SimpleNamespace(log_api_access=lambda **kw: self.rows.append(kw))
        monkeypatch.setattr(sel_mod, "sel", lambda: log)

        rows = self.rows

        class _Log:
            def log(self, event: Any) -> None:
                rows.append(event.metadata | {"resources": event.resources})

        monkeypatch.setattr(cdp, "SecurityEventLog", _Log)


async def _held_run(page: Any, decide: Any, **kw: Any) -> tuple[BrowseLoopResult, list[dict]]:
    """One browse loop run while its work holds :data:`VALUE`, and what its live view was sent;
    knowing *knows*, hosts an allow-list names, beside its start page's site."""
    frames: list[dict] = []
    knows = kw.pop("knows", ())
    with credentials.known_sites("", knows), filled_secrets.handed([VALUE]):
        result = await run_browse_loop(
            goal="read the docs",
            start_url=kw.pop("start_url", START),
            session=kw.pop("session", None) or _FakeSession(page),
            page=page,
            decide=decide,
            max_steps=kw.pop("max_steps", 6),
            on_step=lambda step, _shot: frames.append(asdict(step)),
            **kw,
        )
    return result, frames


def _pieces(value: str) -> list[str]:
    """The runs of letters and digits in *value* long enough to name it, NFKC-folded and
    case-folded, as :func:`_shown_pieces` compares them."""
    folded = unicodedata.normalize("NFKC", value).casefold()
    return [piece for piece in re.split(r"[\W_]+", folded) if len(piece) >= 4]


def _shown_pieces(value: str, *texts: Any) -> list[str]:
    """Which pieces of *value* (:func:`_pieces`) any of *texts* holds, in any case."""
    blob = "\n".join(
        t if isinstance(t, str) else json.dumps(t, default=str, ensure_ascii=False) for t in texts
    )
    folded = unicodedata.normalize("NFKC", blob).casefold()
    return [piece for piece in _pieces(value) if piece in folded]


def _to(url: str) -> str:
    """The line a model sends to take its tab to *url*."""
    goes = next(a for a in sentinels.Action.__args__ if "url" in a.__dataclass_fields__)
    return goes(url=url).render()


@pytest.mark.asyncio
async def test_where_a_held_steps_submission_landed_is_shown_as_its_origin():
    """The note a verified submission records, and every prompt after it, name where it landed by
    its origin. 🔴 Red before: the note held the landing's path and its twice-encoded query."""
    landing = f"{SITE}welcome/PATHMARK-a1?return={quote(quote(VALUE, safe=''), safe='')}"
    page = _FakePage({START: "<html><body><form><input name='q'></form></body></html>"}, url="")

    def _submitted(p: _FakePage) -> None:
        p.url = landing
        p.replace(landing, "<html><body><h1>Welcome</h1></body></html>")

    page.on_submit = _submitted
    decide = _Decide("SUBMIT", "FORM_OK", "DONE")

    result, frames = await _held_run(page, decide)

    assert any("submission verified at " + SITE in note for note in result.notes)
    assert _found(decide.prompts, result.to_payload(), frames) == []


@pytest.mark.asyncio
async def test_a_verified_submission_names_where_it_landed_by_its_origin():
    """The verdict's own note, before anything records it. 🔴 Red before: it named the landing's
    whole address."""
    from personalclaw.browse.loop import VERDICT_FORM_OK, verify_submission

    landing = f"{SITE}welcome/PATHMARK-a1?return=QMARK"
    page = _FakePage({landing: "<html><body><h1>Welcome</h1></body></html>"}, url=landing)

    with credentials.known_sites(START), filled_secrets.handed([VALUE]):
        verdict, note = await verify_submission(
            page=page, decide=_Decide("FORM_OK"), url_before=START, html_before="<p>form</p>"
        )

    assert (verdict, note) == (VERDICT_FORM_OK, f"submission verified at {SITE}")


@pytest.mark.asyncio
async def test_a_page_that_sends_its_tab_to_an_address_with_no_host_shows_only_its_scheme():
    """An address with no host (``about:blank#…``) shows as its scheme alone, in the prompt, the
    run's outputs and its live view. 🔴 Red before: it was shown whole, the value's base64 in it."""
    blank = "about:blank#" + base64.b64encode(VALUE.encode()).decode()

    class _Elsewhere(_FakePage):
        async def current_url(self) -> str:
            return blank

    page = _Elsewhere({START: "<html><body><p>Version 2.0</p></body></html>"}, url="")
    decide = _Decide("DONE")

    result, frames = await _held_run(page, decide)

    assert result.final_url == "about:"
    assert _found(decide.prompts, result.to_payload(), frames) == []


@pytest.mark.asyncio
async def test_a_held_steps_note_shows_each_address_in_it_as_its_origin():
    """A note the model writes shows each address in it as its origin, a relative one withheld.
    🔴 Red before: the note was kept whole."""
    decide = _Decide(
        f"NOTES saw it at {SITE}deep/PATHMARK?q=QMARK#FRAGM and at /rel/PATHMARK/x", "DONE"
    )
    page = _FakePage({START: "<html><body><p>Version 2.0</p></body></html>"}, url="")

    result, frames = await _held_run(page, decide)

    assert result.notes == (f"saw it at {SITE} and at [withheld]",)
    assert _found(result.to_payload(), frames) == []


#: Notes whose address is glued to what comes before it, and what each shows: its origin.
GLUED = {
    "after-a-digit": (f"seen 9{SITE}deep/PATHMARK(QMARK)x", f"seen 9{SITE}"),
    "after-an-underscore": (f"seen _{SITE}a'PATHMARK'QMARK", f"seen _{SITE}"),
    "after-a-letter-no-ascii-has": (f"seen é{SITE}[PATHMARK]QMARK", f"seen é{SITE}"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("glued", sorted(GLUED))
async def test_an_address_glued_to_a_word_shows_as_its_origin(glued):
    """🔴 Red before: it was read as a path from its slashes, up to its first paren, quote or
    bracket, and what followed was kept."""
    written, shown = GLUED[glued]
    decide = _Decide(f"NOTES {written}", "DONE")
    page = _FakePage({START: "<html><body><p>Version 2.0</p></body></html>"}, url="")

    result, frames = await _held_run(page, decide)

    assert result.notes == (shown,), result.notes
    assert _found(result.to_payload(), frames) == []


@pytest.mark.asyncio
async def test_a_held_steps_links_show_their_sites_origin():
    """Each link shows its site's origin, a relative one read against the page's own, in the page's
    text and its Links DSL, when the run knows the site, and a link labelled with its own address
    is labelled with that origin; a site it doesn't know is withheld. 🔴 Red before: a relative
    target, the text's own link addresses, and a label that was its link's address, were shown
    whole."""
    html = (
        "<html><body><p>Docs</p>"
        "<a href='/account/PATHMARK-7q1x/settings#tab=WQXZT'>Account</a>"
        "<a href='//other.example.test/deep/PLMKV'>Other</a>"
        "<a href='https://abs.example.test/deep/ABSPATH-9z/x?q=QMARK#FRAGM'></a>"
        "</body></html>"
    )
    page = _FakePage({START: html}, url="")
    decide = _Decide("DONE")

    await _held_run(page, decide, knows=("other.example.test",))

    (prompt,) = decide.page_prompts
    assert "Account → https://orchard.example.test/" in prompt, prompt
    assert "Other → https://other.example.test/" in prompt, prompt
    assert "[withheld] → [withheld]" in prompt and "abs.example" not in prompt, prompt
    assert _found(decide.prompts) == []


#: A scheme-relative address a page's text writes, with slashes and with backslashes, which a
#: browser reads alike, each carrying markers past its host.
SCHEME_RELATIVE = {
    "with-slashes": "//login.example.test/r/PATHMARK?return=QMARK#FRAGM",
    "with-backslashes": "\\\\login.example.test\\r\\PATHMARK?return=QMARK#FRAGM",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("written", sorted(SCHEME_RELATIVE))
async def test_a_scheme_relative_address_in_a_held_pages_text_shows_no_path(written):
    """With no scheme it shows as `[withheld]`, however its slashes are written. 🔴 Red before
    (with backslashes): it was shown as written. Control: with slashes."""
    html = f"<html><body><p>Docs at {SCHEME_RELATIVE[written]} here</p></body></html>"
    page = _FakePage({START: html}, url="")
    decide = _Decide("DONE")

    await _held_run(page, decide)

    (prompt,) = decide.page_prompts
    assert "Docs at [withheld] here" in prompt, prompt
    assert _found(decide.prompts) == []


#: Held values with a mark, a space, a tab or one of an address's own signs inside them. Each is
#: shown in a page's text, a link's words, a button's and a text box's words and a note, and in
#: each address that holds it, as the page wrote it and as a browser writes it back (a browser
#: refuses a host with white space in it, so for those its text is the page's). Where its
#: words are shown, the rows in :data:`CUT_GUARDS` passed before this rule too: they hold it.
CUT_VALUES = {
    "a-slash-after-a-mark": "Tr0ub4dor&/x7Kq9vB",
    "a-slash-after-a-space": "Tr0ub4dor /x7Kq9vB",
    "a-backslash-after-a-mark": "Tr0ub4dor&\\x7Kq9vB",
    "two-backslashes-after-a-space": "Tr0ub4dor \\\\x7Kq9vB",
    "a-backslash-before-a-markdown-mark": "Kx7q!\\-Qp2w",
    "a-slash": "Tr0ub4dor/x7Kq9vB",
    "an-at-sign": "p@ssw0rdXYZ12",
    "a-question-mark": "Tr0ub4dor?x7Kq9vB",
    "a-hash": "Tr0ub4dor#x7Kq9vB",
    "two-spaces": "Tr0ub4dor  x7Kq9vB",
    "a-tab": "Tr0ub4dor\tx7Kq9vB",
    "a-hash-and-a-space": "Tr0ub4dor#x7 Kq9vB",
    "a-slash-after-a-name": "Mirror4kq/x7Kq9vBz",
}
CUT_GUARDS = frozenset(
    {
        "a-backslash-after-a-mark",
        "a-backslash-before-a-markdown-mark",
        "a-hash",
        "a-question-mark",
        "a-slash",
        "an-at-sign",
        "a-hash-and-a-space",
        "a-slash-after-a-name",
    }
)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", sorted(CUT_VALUES))
async def test_a_held_value_is_kept_whole_while_its_text_is_read_for_addresses(value):
    """A held value is masked whole wherever its words are shown: a page's text, a link's words, a
    button's and a text box's words, and a note. None of it is left beside the mask, in any case.
    🔴 Red before, but for :data:`CUT_GUARDS`: some of it was shown."""
    import html as html_mod

    held = CUT_VALUES[value]
    shown = html_mod.escape(held)
    page_html = (
        f"<html><body><p>Your key is {shown} today</p>"
        f"<a href='/k'>key {shown} here</a>"
        f"<form><button type='button'>use {shown} now</button>"
        f"<textarea name='t'>{shown}</textarea></form></body></html>"
    )
    page = _FakePage({START: page_html}, url="")
    decide = _Decide(f"NOTES your key is {held} today", "DONE")

    with filled_secrets.handed([held]):
        result = await run_browse_loop(
            goal="read", start_url=START, session=_FakeSession(page), page=page, decide=decide
        )

    sent = "\n".join([*decide.prompts, *result.notes])
    assert _shown_pieces(held, sent) == [], sent
    masked = "[REDACTED: credential]"
    for words in (f"Your key is {masked} today", f"use {masked} now"):
        assert words in sent, f"vacuity: {words!r} was shown"
    assert re.search(r"\] key \[REDACTED: credential\] here → ", sent), "vacuity: the link's words"
    assert f"field: t ({masked}) textarea" in sent, "vacuity: the text box's words were shown"
    assert f"your key is {masked} today" in result.notes, result.notes


#: What a browser percent-encodes in an address it writes back, beyond controls and anything past
#: ``~`` (the WHATWG URL standard's sets): in a path, a query and a fragment.
_PATH_ENCODES, _QUERY_ENCODES, _FRAGMENT_ENCODES = ' "#<>?^`{}', " \"#<>'", ' "<>`'


def _encoded(text: str, encodes: str) -> str:
    return "".join(
        "".join(f"%{b:02X}" for b in ch.encode()) if ch in encodes or not " " < ch <= "~" else ch
        for ch in text
    )


def _as_a_browser_writes(address: str) -> str:
    """*address* as a browser writes it back once it has opened it: read as it reads one, its host
    lower-cased and each label whose letters aren't all ASCII as IDNA writes it, a slash after the
    host when nothing follows it but a query or a fragment, and its path, query and fragment
    percent-encoded as the WHATWG URL standard says."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(address.replace("\\", "/"))
    user, at, host = parts.netloc.rpartition("@")
    labels = [
        label if label.isascii() else "xn--" + label.encode("punycode").decode("ascii")
        for label in host.lower().split(".")
    ]
    return urlunsplit(
        parts._replace(
            netloc=f"{user}{at}{'.'.join(labels)}",
            path=_encoded(parts.path, _PATH_ENCODES) or "/",
            query=_encoded(parts.query, _QUERY_ENCODES),
            fragment=_encoded(parts.fragment, _FRAGMENT_ENCODES),
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("value", sorted(CUT_VALUES))
async def test_an_address_that_holds_a_held_value_shows_none_of_it(value):
    """An address that holds a held value, as the page wrote it, with backslashes for its slashes
    (where the value holds no space, which ends an address), or as a browser writes it back, is
    withheld whole: in a page's text, as a link, in a note and as the address of the page a
    submission landed on, since its host is no site the run knew. 🔴 Red before: some of it was
    shown, or its origin was shown with the value as one mask."""
    import html as html_mod

    held = CUT_VALUES[value]
    written = "https://" + held + "/z"
    back = _as_a_browser_writes(written)
    slanted = "" if re.search(r"\s", held) else "https://" + (held + "/z").replace("/", "\\")
    page = _FakePage(
        {
            START: (
                f"<html><body><p>Mirror at {html_mod.escape(back)} now</p>"
                f"<p>Also at {html_mod.escape(slanted or 'the docs')} today</p>"
                f"<a href='{html_mod.escape(written, quote=True)}'>Mirror</a>"
                "<form><input name='q'></form></body></html>"
            )
        },
        url="",
    )

    def _submitted(p: _FakePage) -> None:
        p.url = back
        p.replace(back, "<html><body><h1>Welcome</h1></body></html>")

    page.on_submit = _submitted
    decide = _Decide(f"NOTES mirror at {back}", "SUBMIT", "FORM_OK", "DONE")

    with filled_secrets.handed([held]):
        result = await run_browse_loop(
            goal="read", start_url=START, session=_FakeSession(page), page=page, decide=decide
        )

    shown = "[withheld]"
    prompt, *_ = decide.page_prompts
    assert _shown_pieces(held, decide.prompts, result.notes, result.to_payload()) == []
    assert f"Mirror at {shown} now" in prompt, prompt
    assert f"] Mirror → {shown}" in prompt, prompt
    assert f"Also at {shown} today" in prompt if slanted else "Also at the docs today" in prompt
    assert any("Welcome" in p for p in decide.prompts), "vacuity: the page it landed on was read"
    assert list(result.notes) == [f"mirror at {shown}", f"submission verified at {shown}"]


#: Scheme-relative addresses glued to a word, with slashes and with backslashes.
GLUED_SCHEME_RELATIVE = {
    "after-a-letter": "see x//login.example.test/r/PATHMARK?return=QMARK",
    "after-a-digit": "see 9//login.example.test/r/PATHMARK?return=QMARK",
    "with-backslashes": "see x\\\\login.example.test\\r\\PATHMARK?return=QMARK",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("written", sorted(GLUED_SCHEME_RELATIVE))
async def test_a_scheme_relative_address_glued_to_a_word_shows_no_path(written):
    """🔴 Red before: a scheme-relative address glued to a word was shown as written."""
    html = f"<html><body><p>{GLUED_SCHEME_RELATIVE[written]} now</p></body></html>"
    page = _FakePage({START: html}, url="")
    decide = _Decide("DONE")

    await _held_run(page, decide)

    (prompt,) = decide.page_prompts
    assert "[withheld] now" in prompt, prompt
    assert _found(decide.prompts) == []


@pytest.mark.asyncio
async def test_a_held_links_target_is_read_as_a_browser_reads_it():
    """A target written with backslashes is read as a browser reads it before it is joined to the
    page's address, so the Links DSL shows the site a browser would open (one the run knows). 🔴
    Red before: it was joined as a path of the page's own site."""
    html = (
        "<html><body><p>Docs</p><a href='\\\\other.example.test\\PATHMARK'>Other</a></body></html>"
    )
    page = _FakePage({START: html}, url="")
    decide = _Decide("DONE")

    await _held_run(page, decide, knows=("other.example.test",))

    (prompt,) = decide.page_prompts
    assert "Other → https://other.example.test/" in prompt, prompt
    assert _found(decide.prompts) == []


#: Held values a host carries in other letters than they are written in: each with the host a
#: page or a model writes, and the host a browser writes for it, as a browser writes it (UTS46
#: nontransitional, then IDNA), written out here and never built with the code under test. Lower-
#: cased; with letters that aren't all ASCII, as a whole label, inside one and across a dot; with
#: letters a browser maps or folds, the code points it drops, the full stops it reads as a dot, a
#: percent escape it decodes, the signs it keeps in a label, and a number or an IPv6 address it
#: reads as an address.
SITE_HOST = ".orchard.example.test"
HOST_VALUES = {
    "lower-cased": ("Tr0ub4dorQx", "Tr0ub4dorQx", "tr0ub4dorqx"),
    "idna-a-whole-label": ("Pässwörtchen", "Pässwörtchen", "xn--psswrtchen-q5a3t"),
    "idna-inside-a-label": ("Pässwörtchen", "zPässwörtchenz", "xn--zpsswrtchenz-hcb9w"),
    "idna-across-a-dot": ("Pä.ss9x", "Pä.ss9x", "xn--p-0fa.ss9x"),
    "a-sharp-s": ("Straße9Kx", "Straße9Kx", "xn--strae9kx-tya"),
    "a-final-sigma": ("Σοφίαςk7", "Σοφίαςk7", "xn--k7-48bi2euadz"),
    "a-capital-sigma": ("K7ΣΟΦΙΑΣ", "K7ΣΟΦΙΑΣ", "xn--k7-d9b5a3axdv"),
    "full-width": ("ｔｏｋｅｎ12345x", "ｔｏｋｅｎ12345x", "token12345x"),
    "a-soft-hyphen": ("Tr0ub\u00adx7Kq9", "Tr0ub\u00adx7Kq9", "tr0ubx7kq9"),
    "an-ideographic-full-stop": ("Tr0ub4dor\u3002x7Kq", "Tr0ub4dor\u3002x7Kq", "tr0ub4dor.x7kq"),
    "a-halfwidth-full-stop": ("Tr0ub4dor\uff61x7Kq", "Tr0ub4dor\uff61x7Kq", "tr0ub4dor.x7kq"),
    "a-variation-selector": (
        "I\u2764\ufe0fPizza2024",
        "I\u2764\ufe0fPizza2024",
        "xn--ipizza2024-co3g",
    ),
    "a-percent-escape": ("Pass%41word12", "Pass%41word12", "passaword12"),
    "a-hangul-filler": ("Tr0ub\u3164x7Kq9", "Tr0ub\u3164x7Kq9", "tr0ubx7kq9"),
    "a-variation-selector-17": ("Tr0ub\U000e0100x7Kq9", "Tr0ub\U000e0100x7Kq9", "tr0ubx7kq9"),
    "a-capital-sharp-s": ("Stra\u1e9e9Kx", "Stra\u1e9e9Kx", "xn--stra9kx-3va"),
    "a-full-width-capital-sharp-s": (
        "\uff33\uff34\uff32\uff21\u1e9e9Kx",
        "\uff33\uff34\uff32\uff21\u1e9e9Kx",
        "xn--stra9kx-3va",
    ),
    "idna-and-an-underscore": ("Pässwort_42", "Pässwort_42", "xn--psswort_42-q5a"),
    "idna-and-a-dollar": ("Müller$2024!", "Müller$2024!", "xn--mller$2024!-thb"),
    "idna-and-an-apostrophe": ("Pässwort'42", "Pässwort'42", "xn--psswort'42-q5a"),
    "idna-and-a-quote": ('Pä"ss9xyz', 'Pä"ss9xyz', 'xn--p"ss9xyz-0za'),
    "idna-and-a-parenthesised-letter": ("Päss\u3200x9", "Päss\u3200x9", "xn--pss()x9-5wa3328a"),
    "a-tab-before-a-combining-mark": ("Pa\t\u0308ss9xyz", "Pa\t\u0308ss9xyz", "xn--pss9xyz-5wa"),
    "a-tab-between-conjoining-jamo": (
        "Tr0\u1100\t\u1161x9",
        "Tr0\u1100\t\u1161x9",
        "xn--tr0x9-rb9s",
    ),
}
#: Held values read as the whole host: a number, as the address a browser reads it as, and an IPv6
#: address, as a browser writes it.
WHOLE_HOSTS = {
    "a-number": ("12345678", "12345678", "0.188.97.78"),
    "an-ipv6": ("2001:DB8:0:0:0:0:0:1", "[2001:DB8:0:0:0:0:0:1]", "[2001:db8::1]"),
}


def _hosts(name: str) -> tuple[str, str, str]:
    """*name*'s held value, the host written and the host a browser writes, on the test site but
    for :data:`WHOLE_HOSTS`, which are the whole host."""
    if name in WHOLE_HOSTS:
        return WHOLE_HOSTS[name]
    value, written, browser = HOST_VALUES[name]
    return value, written + SITE_HOST, browser + SITE_HOST


#: Held values that hold one of an address's own signs, so the host a guard or a browser reads is
#: a piece of one: the address opened as written, and the frame's address as the browser reports
#: it (CDP's frame address carries no fragment; a host's letters as IDNA writes them, a number as
#: the address it reads one as, an IPv6 address as Node's WHATWG URL writes it). Where only the
#: rule for a value cut at a sign could mask the piece, the frame's address is its origin alone.
SIGNED_HOST_VALUES = {
    "an-at-sign": (
        "Tr0ub4dor@x7Kq9vB",
        f"https://Tr0ub4dor@x7Kq9vB{SITE_HOST}/x",
        f"https://Tr0ub4dor@x7kq9vb{SITE_HOST}/y",
    ),
    "a-slash": (
        "Tr0ub4dor/x7Kq9vB",
        f"https://Tr0ub4dor/x7Kq9vB{SITE_HOST}/x",
        f"https://tr0ub4dor/x7Kq9vB{SITE_HOST}/y",
    ),
    "a-hash": (
        "Tr0ub4dor#x7Kq9vB",
        f"https://Tr0ub4dor#x7Kq9vB{SITE_HOST}/x",
        "https://tr0ub4dor/",
    ),
    "idna-and-a-slash": (
        "Tr0ub4dör/x7Kq9vB",
        "https://Tr0ub4dör/x7Kq9vB",
        "https://xn--tr0ub4dr-t4a/x7Kq9vB",
    ),
    "userinfo-before-the-value": (
        "Tr0ub4dor@x7Kq9vB",
        f"https://me:Tr0ub4dor@x7Kq9vB{SITE_HOST}/x",
        f"https://me:Tr0ub4dor@x7kq9vb{SITE_HOST}/y",
    ),
    "userinfo-dropped": (
        "Tr0ub4dor@x7Kq9vB",
        f"https://Tr0ub4dor@x7Kq9vB{SITE_HOST}/x",
        f"https://x7kq9vb{SITE_HOST}/y",
    ),
    "a-lunate-sigma-and-a-slash": (
        "Tr0ub\u03f2x7/Kq9vB",
        f"https://Tr0ub\u03f2x7/Kq9vB{SITE_HOST}/x",
        "https://xn--tr0ubx7-9pf/",
    ),
    "a-capital-sharp-s-and-a-slash": (
        "Tr0ub\u1e9ex7/Kq9vB",
        f"https://Tr0ub\u1e9ex7/Kq9vB{SITE_HOST}/x",
        "https://xn--tr0ubx7-4va/",
    ),
    "an-invisible-plus-and-a-slash": (
        "Tr0ub\u2064x7/Kq9vB",
        f"https://Tr0ub\u2064x7/Kq9vB{SITE_HOST}/x",
        "https://tr0ubx7/",
    ),
    "a-number-and-a-slash": ("4711/2024x", "https://4711/2024x", "https://0.0.18.103/"),
    "a-number-and-a-hash": ("12345678#abcXYZ", "https://12345678#abcXYZ", "https://0.188.97.78/"),
    "a-hex-number": ("0x7f0000AB#qwe", "https://0x7f0000AB#qwe", "https://127.0.0.171/"),
    "a-number-after-the-at": ("Pw@4711", "https://u:Pw@4711/", "https://0.0.18.103/"),
    "a-two-part-number": ("192.168#xYz9", "https://192.168#xYz9", "https://192.0.0.168/"),
    "a-short-lead": ("q7z#x7Kq9vBzz", "https://q7z#x7Kq9vBzz", "https://q7z/"),
    "a-tab-and-a-hash": (
        "Tr0u\tb4dor#x7Kq9vB",
        "https://Tr0u\tb4dor#x7Kq9vB",
        "https://tr0ub4dor/",
    ),
    "an-address-and-a-hash": (
        "https://x7Kq9vB2#frag9",
        "https://x7Kq9vB2#frag9",
        "https://x7kq9vb2/",
    ),
    "an-ipv6-after-the-at": (
        "Pw@[2001:DB8::0:1]",
        "https://u:Pw@[2001:DB8::0:1]/",
        "https://u:Pw@[2001:db8::1]/",
    ),
    "an-ipv6-and-a-port": (
        "Pw@[2001:DB8::0:1]:8443",
        "https://u:Pw@[2001:DB8::0:1]:8443/",
        "https://u:Pw@[2001:db8::1]:8443/",
    ),
    "an-address-with-an-ipv6": (
        "https://[2001:DB8:0::1]/x",
        "https://[2001:DB8:0::1]/x",
        "https://[2001:db8::1]/x",
    ),
    "an-ipv6-head-after-the-at": (
        "Tr0ub4dor@[2001:0DB8:85a3",
        "https://Tr0ub4dor@[2001:0DB8:85a3::1]/x",
        "https://Tr0ub4dor@[2001:db8:85a3::1]/x",
    ),
    "an-ipv4-mapped-into-ipv6": (
        "10.0.0.5",
        "https://[::ffff:10.0.0.5]/x",
        "https://[::ffff:a00:5]/x",
    ),
    "an-ipv4-in-a-nat64-prefix": (
        "10.0.0.5",
        "https://[64:ff9b::10.0.0.5]/x",
        "https://[64:ff9b::a00:5]/x",
    ),
    "the-last-piece-inside-a-label": (
        "https://x7Kq9vB2",
        "http://x7Kq9vB2zz.orchard.example.test/x",
        "http://x7kq9vb2zz.orchard.example.test/x",
    ),
    "the-last-piece-after-two-slashes": (
        "Tr0ub4dor//x7Kq9vB2",
        "http://x7Kq9vB2zz.evil.test/y",
        "http://x7kq9vb2zz.evil.test/y",
    ),
    "userinfo-dropped-inside-a-label": (
        "Tr0ub4dor@x7Kq9vB",
        f"https://Tr0ub4dor@x7Kq9vBzz{SITE_HOST}/x",
        f"https://x7kq9vbzz{SITE_HOST}/y",
    ),
}
#: Every row the refusal tests open: (held, opened, the frame's address).
REFUSED = {
    **{
        name: (_hosts(name)[0], f"https://{_hosts(name)[1]}/x", f"https://{_hosts(name)[2]}/y")
        for name in [*HOST_VALUES, *WHOLE_HOSTS]
    },
    **SIGNED_HOST_VALUES,
}


def _browser_host(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or ""


def _in_letters(value: str, host: str, *texts: Any) -> list[str]:
    """Which letters of *value* any of *texts* holds, in any case: a piece of the value
    (:func:`_shown_pieces`), each label of *host* IDNA writes it in, and *host* itself, without the
    test site's own name (the value or a piece of it as a browser wrote it: a number as the address
    it reads one as)."""
    blob = json.dumps(texts, default=str, ensure_ascii=False).casefold()
    ace = [label for label in host.split(".") if label.startswith("xn--") and label in blob]
    written = host.removesuffix(SITE_HOST).casefold()
    return sorted(
        {*_shown_pieces(value, blob), *ace, *([written] if written in blob else [])} - {""}
    )


def _listed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every session's egress tier narrowed to an allow-list the test site isn't on."""
    from personalclaw.browse import cdp
    from personalclaw.net.policy import EgressPolicy

    monkeypatch.setattr(
        cdp.GatedCdpSession,
        "_layered_policy",
        lambda self: EgressPolicy(
            name="listed", allow_only=True, allow_hosts=("elsewhere.example.test",)
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["browse", "listed"])
@pytest.mark.parametrize("held", sorted(REFUSED))
async def test_a_refused_address_whose_host_holds_a_value_names_no_letter_of_it(
    monkeypatch, held, tier
):
    """An address a model opens, or one the page opens itself, whose host holds a held value in
    other letters or holds a piece of one is refused, under browse's own tier and an allow-list,
    and neither its audit rows nor what the run is told of it hold that value in any letters.
    🔴 Red before: some of it was named."""
    from test_browse_cdp_preflight import FakeTransport, _frame, _started

    from personalclaw.browse import cdp

    value, opened, framed = REFUSED[held]
    host = _browser_host(framed)
    rows = _Rows(monkeypatch)
    if tier == "listed":
        _listed(monkeypatch)
    with credentials.known_sites(START), filled_secrets.handed([value]):
        transport = FakeTransport()
        session = await _started(transport)
        refused = await session.navigate(opened)
        await transport.listener(cdp.FRAME_NAVIGATED, _frame(framed))
        for _ in range(100):
            if len(session.blocks) >= 2:
                break
            await asyncio.sleep(0.01)

    told = [(b.reason, b.recovery_hints) for b in session.blocks]
    named = [row["host"] for row in rows.rows if row["host"]]
    assert not refused.ok and len(session.blocks) >= 2 and len(named) >= 2, rows.rows
    assert _in_letters(value, host, rows.rows) == [], rows.rows
    assert _in_letters(value, host, told) == [], told
    assert named == ["[withheld]"] * len(named), named


@pytest.mark.parametrize("held", sorted(REFUSED))
def test_an_audit_row_names_no_letter_of_a_held_value_whatever_it_is_handed(monkeypatch, held):
    """An audit row is masked of a held value in any letters, and names the host browse shows,
    whatever outcome it is handed: one whose host and reason name the host a guard read."""
    from test_browse_cdp_preflight import FakeTransport, _session

    from personalclaw.browse import cdp

    value, _opened, url = REFUSED[held]
    host = _browser_host(url)
    read = cdp._host_of(url)
    rows = _Rows(monkeypatch)
    session = _session(FakeTransport())
    with credentials.known_sites(START), filled_secrets.handed([value]):
        session._audit(
            cdp.NavigationOutcome(
                ok=False, allowed=False, url=url, host=read, reason=f"host {read!r} is refused"
            ),
            operation=cdp.NAVIGATE,
            phase="preflight",
        )

    assert rows.rows and rows.rows[0]["host"], "vacuity: a row was written"
    assert _in_letters(value, host, rows.rows) == [], rows.rows


def test_a_refusal_the_run_is_told_is_masked_whatever_reason_it_is_handed():
    """What a refusal tells the run, its reason and each hint, is masked of a held value as written
    as well as in the host it names, whatever reason the guard gave."""
    from test_browse_cdp_preflight import FakeTransport, _session

    from personalclaw.browse import cdp
    from personalclaw.net.guard import GuardDecision

    held = "Tr0ub4dorQx7"
    session = _session(FakeTransport())
    with credentials.known_sites(START), filled_secrets.handed([held]):
        told = session._record_block(
            GuardDecision(
                allow=False,
                url="https://orchard.example.test/x",
                host="orchard.example.test",
                reason=f"refused on {held} at orchard.example.test",
                risk_level="destructive",
                recovery_hints=[f"drop {held} from the address"],
            ),
            operation=cdp.NAVIGATE,
            phase="preflight",
        )

    assert "orchard.example.test" in told.reason, "vacuity: the reason was kept"
    assert _shown_pieces(held, told.reason, told.recovery_hints) == [], told


@pytest.mark.asyncio
@pytest.mark.parametrize("held", ["lower-cased", "a-sharp-s", "an-at-sign", "a-hash"])
async def test_a_refused_address_the_run_is_told_of_names_no_letter_of_it(monkeypatch, held):
    """The line a run is told when the address it opened is refused names that address and why,
    neither holding a held value in any letters. 🔴 Red before: some of it was named."""
    from test_browse_cdp_preflight import FakeTransport, _inline

    from personalclaw.browse import cdp

    value, opened, framed = REFUSED[held]
    _listed(monkeypatch)
    page = _FakePage({START: "<html><body><p>Docs</p></body></html>"}, url="")
    gated = cdp.GatedCdpSession(
        FakeTransport(), resolver=lambda h: ["93.184.216.40"], blocking=_inline
    )

    class _Session:
        async def start(self) -> None:
            await gated.start()

        async def navigate(self, url: str) -> Any:
            if url == START:
                page.url = url
                return types.SimpleNamespace(ok=True, allowed=True, url=url, reason="", error="")
            return await gated.navigate(url)

    decide = _Decide(_to(opened), "DONE")

    with filled_secrets.handed([value]):
        await run_browse_loop(
            goal="read", start_url=START, session=_Session(), page=page, decide=decide
        )

    assert any("was refused" in p for p in decide.prompts), "vacuity: the run was told"
    assert _in_letters(value, _browser_host(framed), decide.prompts) == [], decide.prompts


#: Held values the part of which before a colon is a scheme: (held, the frame's address as the
#: browser reports it, its fragment dropped and its tabs).
SCHEME_VALUES = {
    "a-colon": ("Tr0ub4dor:x7Kq9vB", "tr0ub4dor:x7Kq9vB"),
    "a-colon-and-a-hash": ("Tr0ub4dor:x7Kq#9vB", "tr0ub4dor:x7Kq"),
    "a-tab-and-a-colon": ("Tr0u\tb4dor:x7Kq9vB", "tr0ub4dor:x7Kq9vB"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("value", sorted(SCHEME_VALUES))
async def test_a_frame_whose_scheme_is_a_piece_of_a_held_value_names_none_of_it(monkeypatch, value):
    """A frame the page opens on an address whose scheme is the part of a held value before its
    colon is refused, and neither what the run is told nor its audit row names that scheme.
    🔴 Red before: the scheme was named."""
    from test_browse_cdp_preflight import FakeTransport, _frame, _started

    from personalclaw.browse import cdp

    held, framed = SCHEME_VALUES[value]
    rows = _Rows(monkeypatch)
    with filled_secrets.handed([held]):
        transport = FakeTransport()
        session = await _started(transport)
        await transport.listener(cdp.FRAME_NAVIGATED, _frame(framed))
        for _ in range(100):
            if session.blocks:
                break
            await asyncio.sleep(0.01)

    told = [(b.reason, b.recovery_hints) for b in session.blocks]
    assert session.blocks and rows.rows, "vacuity: the frame was refused and audited"
    assert _in_letters(held, "", told, rows.rows) == [], (told, rows.rows)


@pytest.mark.asyncio
async def test_two_refused_frames_that_show_alike_keep_a_row_each(monkeypatch):
    """Two of a page's frames on different hosts, both withheld while a value is held, are each
    refused and audited: they are counted by the host the guard read, not the one shown. 🔴 Red
    before: the second was counted on the first, with no row of its own."""
    from test_browse_cdp_preflight import FakeTransport, _started

    from personalclaw.browse import cdp

    rows = _Rows(monkeypatch)
    with filled_secrets.handed(["Tr0ub4dor#x7Kq9vB"]):
        transport = FakeTransport()
        session = await _started(transport)
        for url in ("https://tr0ub4dor/", "https://mytr0ub4dor/"):
            frame = {"id": url[8:-1], "parentId": "TOP", "url": url}
            await transport.listener(cdp.FRAME_NAVIGATED, {"frame": frame})
        for _ in range(100):
            if len(rows.rows) >= 2:
                break
            await asyncio.sleep(0.01)

    hosts = [row["host"] for row in rows.rows]
    assert hosts == ["[withheld]", "[withheld]"], rows.rows
    assert len(session.blocks) == 2, session.blocks


def test_a_host_a_held_value_moves_is_withheld():
    """The host a guard read is withheld when a held value in the address moves where the host
    starts: here a login before it ends inside the value."""
    from personalclaw.browse.credentials import shown_host

    with filled_secrets.handed(["Tr0ub4dor@x7Kq9vB"]):
        assert shown_host(f"https://me:Tr0ub4dor@x7Kq9vB{SITE_HOST}/", f"x7kq9vb{SITE_HOST}") == (
            "[withheld]"
        )
    with filled_secrets.handed(["Tr0ub4dor#x7Kq9vB"]):
        assert shown_host("https://tr0ub4dor/#Tr0ub4dor#x7Kq9vB", "tr0ub4dor") == "[withheld]"


@pytest.mark.asyncio
@pytest.mark.parametrize("held", sorted([*HOST_VALUES, *WHOLE_HOSTS]))
async def test_an_address_whose_host_holds_a_value_shows_no_letter_of_it(held):
    """A page's text, a link and a note naming an address whose host holds a held value in other
    letters show it withheld whole. 🔴 Red before: some of it was shown, or its origin was shown
    masked."""
    import html as html_mod

    value, _written, host = _hosts(held)
    shown = html_mod.escape(host)
    html = (
        f"<html><body><p>Mirror at https://{shown}/PATHMARK now</p>"
        f"<a href='https://{shown}/QMARK'>Mirror</a></body></html>"
    )
    page = _FakePage({START: html}, url="")
    decide = _Decide(f"NOTES mirror at https://{host}/FRAGM", "DONE")

    with filled_secrets.handed([value]):
        result = await run_browse_loop(
            goal="read", start_url=START, session=_FakeSession(page), page=page, decide=decide
        )

    prompt = decide.page_prompts[0]
    assert _in_letters(value, _browser_host(f"https://{host}/"), decide.prompts, result.notes) == []
    assert "Mirror at [withheld] now" in prompt, prompt
    assert re.search(r"\] Mirror → \[withheld\]", prompt), "vacuity: the link"
    assert "mirror at [withheld]" in result.notes, result.notes
    assert _found(decide.prompts, result.notes) == []


def test_an_origin_names_only_a_site_the_run_knows():
    """While a value is held, an origin names a site the run knew before it touched the browser, as
    a browser writes its host, on its scheme's own port; a page's own address, its other hosts and
    a host the run doesn't know are withheld whole. Green before too, and under any rule: the
    family's control."""
    with credentials.known_sites(START), filled_secrets.handed([VALUE]):
        assert credentials.origin_of(f"{SITE}a?k=1") == SITE
        assert credentials.origin_of("http://orchard.example.test/other") == (
            "http://orchard.example.test/"
        )
        assert credentials.origin_of("https://u:Kx7q@orchard.example.test/") == SITE
        assert credentials.screen_url(f"{SITE}docs?k={VALUE}") == SITE


def test_an_unknown_host_is_withheld_whole():
    """A host the run doesn't know is withheld whole wherever browse names it: its origin, its
    screened address and the host a refusal names. 🔴 Red before: its origin was shown."""
    frame = "https://mirror.example.test/y"
    with credentials.known_sites(START), filled_secrets.handed([VALUE]):
        assert credentials.origin_of(frame) == "[withheld]"
        assert credentials.screen_url(frame) == "[withheld]"
        assert credentials.shown_host(frame, "mirror.example.test") == "[withheld]"


def test_an_unknown_hosts_audit_row_names_it_withheld(monkeypatch):
    """The audit row of a refusal of a host the run doesn't know names it withheld. 🔴 Red before:
    it named the host."""
    from test_browse_cdp_preflight import FakeTransport, _session

    from personalclaw.browse import cdp
    from personalclaw.net.guard import GuardDecision

    rows = _Rows(monkeypatch)
    session = _session(FakeTransport())
    frame = "https://mirror.example.test/y"
    with credentials.known_sites(START), filled_secrets.handed([VALUE]):
        session._record_block(
            GuardDecision(
                allow=False,
                url=frame,
                host="mirror.example.test",
                reason="host 'mirror.example.test' is not resolvable",
                category="unresolvable",
            ),
            operation=cdp.NAVIGATE,
            phase="preflight",
        )

    assert [row["host"] for row in rows.rows] == ["[withheld]"], rows.rows
    assert "mirror" not in json.dumps(rows.rows), rows.rows
    assert session.blocks[0].recovery_hints == [
        "Check the hostname; the fetch fails closed on an unresolvable host.",
        "To see which host it was, run the step once without its secret.",
    ], session.blocks[0].recovery_hints


def test_with_no_site_known_every_address_is_withheld():
    """A run that knows no site shows none, its start page's among them. 🔴 Red before: the start
    page's origin was shown."""
    with filled_secrets.handed(["Kx7q9vB"]):
        assert credentials.origin_of(START) == "[withheld]"
        assert credentials.shown_host(START, "orchard.example.test") == "[withheld]"


#: Held values that coincide with the run's own start site: a label of it, a value with a piece
#: equal to a label of it, and a value with no sign that holds four letters in a row of it.
COINCIDING = {
    "a-label": "orchard",
    "a-piece": "Tr0ub@example",
    "a-label-inside-a-value": "orchard2024!",
}


@pytest.mark.parametrize("held", sorted(COINCIDING))
def test_a_known_site_a_held_value_coincides_with_is_withheld_whole(held):
    """A site the run knows is withheld whole, never shown in part, while a held value coincides
    with it. 🔴 Red before: it was shown with the value masked in it."""
    with credentials.known_sites(START), filled_secrets.handed([COINCIDING[held]]):
        assert credentials.origin_of(START) == "[withheld]"
        assert credentials.shown_host(START, "orchard.example.test") == "[withheld]"


def test_an_address_with_no_host_shows_its_scheme_only_when_browse_names_it():
    """An address with no host shows its scheme alone when it is one browse names (``about:``), and
    is withheld when it isn't. 🔴 Red before: the other scheme was shown masked."""
    with credentials.known_sites(START), filled_secrets.handed(["Tr0ub4dorQx"]):
        assert credentials.origin_of("about:blank") == "about:"
        assert credentials.origin_of("tr0ub4dorqx:x") == "[withheld]"


#: Ports a held value's number ends or starts: the address opened as Node's WHATWG URL writes it.
PORTS = {
    "a-port-ending-in-a-held-number": (
        "4433#abcdefg",
        "https://orchard.example.test:14433/#abcdefg",
    ),
    "a-port-starting-with-a-held-number": ("8443#abcdefg", "https://orchard.example.test:18443/"),
}


@pytest.mark.parametrize("port", sorted(PORTS))
def test_a_port_the_run_was_not_set_to_open_is_withheld(port):
    """A port of a known site that the run's own address didn't write is withheld with the rest.
    🔴 Red before: the port was shown."""
    held, opened = PORTS[port]
    with credentials.known_sites(START), filled_secrets.handed([held]):
        assert credentials.origin_of(opened) == "[withheld]"
        assert credentials.screen_url(opened) == "[withheld]"


def test_the_port_the_run_was_set_to_open_is_shown_unless_a_held_value_is_in_it():
    """The port the run's own start page wrote is shown with its site, and withheld while a held
    number is in it. 🔴 Red before: the second was shown masked or whole."""
    known = "https://orchard.example.test:8443/x"
    with credentials.known_sites(known):
        with filled_secrets.handed(["Zz9q#Kx7q"]):
            assert credentials.origin_of("https://orchard.example.test:8443/y") == (
                "https://orchard.example.test:8443/"
            )
        with filled_secrets.handed(["8443#abcdefg"]):
            assert credentials.origin_of("https://orchard.example.test:8443/y") == "[withheld]"
    with credentials.known_sites("https://orchard.example.test:8000/x"):
        with filled_secrets.handed(["Zz#00"]):
            assert credentials.origin_of("https://orchard.example.test:8000/y") == "[withheld]"


#: The tags a page stresses a word with, and held values whose marks ``html_to_text`` escapes.
STRESSED = {
    f"{tag}-{name}": (tag, held)
    for tag in ("b", "strong", "em", "del")
    for name, held in (
        ("a-mark", "Tr0ub4dor&x7Kq9vB"),
        ("a-mark-and-a-slash", "Tr0ub4dor&/x7Kq9vB"),
    )
}


@pytest.mark.asyncio
@pytest.mark.parametrize("stressed", sorted(STRESSED))
async def test_a_held_value_a_page_stresses_is_masked_whole(stressed):
    """A held value inside a tag that stresses it is masked whole in the page's text, its marks
    kept around the mask. 🔴 Red before: some of it was shown."""
    import html as html_mod

    tag, held = STRESSED[stressed]
    html = f"<html><body><p>Key <{tag}>{html_mod.escape(held)}</{tag}> today</p></body></html>"
    page = _FakePage({START: html}, url="")
    decide = _Decide("DONE")

    with filled_secrets.handed([held]):
        await run_browse_loop(
            goal="read", start_url=START, session=_FakeSession(page), page=page, decide=decide
        )

    (prompt,) = decide.page_prompts
    assert _shown_pieces(held, prompt) == [], prompt
    assert re.search(r"Key \S*\[REDACTED: credential\]\S* today", prompt), "vacuity: it was read"


def _field_ref(html: str) -> str:
    from personalclaw.browse.extraction import extract_page

    return extract_page(html, url=START).forms[0].fields[0].ref


#: Each line a model repeats until the run parks, and what it must not leave behind.
REPEATED = {
    "taking-its-tab-to-a-deep-address": _to(f"{SITE}deep/PATHMARK-77?view=QMARK"),
    "typing-the-value": "TYPE {ref} (" + VALUE + ")",
    "taking-its-tab-to-a-parenthesised-path": _to(f"{SITE}wiki/A(PATHMARK)?return=QMARK"),
    "taking-its-tab-to-a-quoted-path": _to(f"{SITE}a'PATHMARK?return=QMARK"),
    "taking-its-tab-to-an-ipv6-host-with-a-port": _to("http://[2001:db8::1]:8080/PATHMARK?r=QMARK"),
    "describing-a-click-it-may-not-make": "CLICK_VISION the link at "
    + SITE
    + "PATHMARK?k="
    + VALUE,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("line", sorted(REPEATED))
async def test_a_held_steps_recorded_lines_show_no_address_or_value(monkeypatch, line):
    """The lines a step records (its outputs, its live view, the park's audit row) show each
    address as its origin and are masked of the value. 🔴 Red before: the recorded line held the
    path and query, or the typed value, in the audit row and the live view."""
    html = (
        "<html><body><form><label>Search <input name='q' type='text'></label></form>"
        "</body></html>"
    )
    rows = _Rows(monkeypatch)
    page = _FakePage({START: html}, url="")
    said = REPEATED[line].replace("{ref}", _field_ref(html))
    page.replace(f"{SITE}deep/PATHMARK-77?view=QMARK", html)
    decide = _Decide(fallback=said)

    result, frames = await _held_run(page, decide, max_steps=8)

    assert result.parked, "the run parks when it repeats itself"
    assert rows.rows, "the park was audited"
    if line == "describing-a-click-it-may-not-make":
        refused = [r for r in rows.rows if "refused_not_opted_in" in json.dumps(r, default=str)]
        assert refused, "vacuity: the click it may not make was audited"
    assert _found(result.to_payload(), frames, rows.rows) == []


#: Runs of 200,000 characters with no address in them, by name, so no test id holds one.
LONG_RUNS = {
    "letters": "x" * 200_000,
    "letters-and-dots": "a." * 100_000,
    "digits": "1a" * 100_000,
    "idna-labels": "xn--" * 50_000,
}


@pytest.mark.parametrize("name", sorted(LONG_RUNS))
def test_a_long_run_of_text_with_no_address_is_read_at_once(name):
    """Reading a page's text for its addresses takes time in step with its length. 🔴 Red before:
    a scheme of any length, glued to anything, made it quadratic: 0.67 s at 32,000 characters."""
    import cpu_budget

    from personalclaw.browse.credentials import addresses_as_origins

    run = LONG_RUNS[name]
    assert addresses_as_origins(run) == run
    took = cpu_budget.seconds(lambda: addresses_as_origins(run))
    assert took < cpu_budget.limit(1.0), f"{took:.2f}s"


def test_a_glued_name_and_value_is_no_address():
    """A name and a value joined by a colon, the name glued after an underscore or a digit, are
    shown as they are, while a scheme a browser opens or runs is an address however it is glued.
    🔴 Red before: each was cut at its colon."""
    from personalclaw.browse.credentials import addresses_as_origins

    kept = "user_id:12345 at 2026-11-07T12:30:00Z"
    with filled_secrets.handed([VALUE]):
        assert addresses_as_origins(kept) == kept
        assert addresses_as_origins("x_mailto:a@b.test?body=QMARK") == "x_[withheld]"


@pytest.mark.asyncio
async def test_a_held_step_taking_its_tab_to_three_addresses_on_one_site_is_not_stuck():
    """The stuck detector reads each line the model sends as it was sent, not as its origin, so
    three addresses on one site are three actions. 🔴 Red before (a detector reading origins):
    the third was warned as a repeat."""
    deep = [f"{SITE}deep/{n}" for n in ("one", "two", "three")]
    html = "<html><body><p>Version 2.0</p></body></html>"
    page = _FakePage({START: html, **{url: html for url in deep}}, url="")
    decide = _Decide(*(_to(url) for url in deep), "DONE")

    result, _frames = await _held_run(page, decide)

    assert not result.parked and len(decide.page_prompts) == 4, "vacuity: each address was read"
    assert not any("You appear stuck" in prompt for prompt in decide.prompts)


@pytest.mark.asyncio
async def test_a_denied_address_with_no_host_is_audited_and_quarantined_as_its_scheme(monkeypatch):
    """A page that commits an address with no host is audited with its scheme alone, and the
    session it quarantines says so in the same words. 🔴 Red before: the audit row and the
    quarantine named the whole address, the value as written in it."""
    from test_browse_cdp_preflight import FakeTransport, _frame, _started

    from personalclaw.browse import cdp

    rows = _Rows(monkeypatch)
    with filled_secrets.handed([VALUE]):
        transport = FakeTransport(fail_on={cdp.STOP_LOADING})
        session = await _started(transport)
        await transport.listener(cdp.FRAME_NAVIGATED, _frame("data:text/html," + VALUE))
        for _ in range(100):
            if session.quarantine_reason:
                break
            await asyncio.sleep(0.01)

    assert "'data:'" in session.quarantine_reason, session.quarantine_reason
    assert rows.rows, "the commit was audited"
    assert [row["url"] for row in rows.rows] == ["data:"] * len(rows.rows)
    assert _found(rows.rows, session.quarantine_reason) == []


def test_the_done_sentence_is_masked_before_its_cut():
    """A finished run's sentence is masked of the value before it is cut, so no piece of it is
    left at the cut. 🔴 Red before: the sentence held the value."""
    from personalclaw.action_providers.browse_provider import BrowseActionProvider

    note = "x" * 290 + " " + VALUE + " and more"
    result = BrowseLoopResult(ok=True, goal="g", final_url=SITE, notes=(note,))

    with filled_secrets.handed([VALUE]):
        said = BrowseActionProvider._done_sentence(result)

    assert said.endswith("…")
    assert _found(said) == []


def test_the_vision_park_says_why_and_what_to_do():
    """🔴 Red before: no park said why or what to do; the screenshot was sent."""
    from personalclaw.action_providers.browse_provider import BrowseActionProvider
    from personalclaw.browse import vision
    from personalclaw.browse.loop import PARK_VISION_UNAVAILABLE

    result = BrowseLoopResult(
        ok=True,
        goal="g",
        final_url=SITE,
        parked=True,
        park_reason=PARK_VISION_UNAVAILABLE,
        park_detail=vision.REASON_HOLDS_A_SECRET,
    )

    said = BrowseActionProvider._park_sentence(result)

    assert said.startswith(
        "Browse stopped: vision grounding is off while this step holds a stored secret's value, "
        "since a screenshot could show it"
    )
    assert said.endswith(
        " Take the {{secret:…}} out of the start page, or turn vision grounding off for this step."
    )


@pytest.mark.asyncio
async def test_a_goal_that_names_a_secret_is_refused_saying_why_and_what_to_do():
    """A fire's browse refuses such a goal in its words: what, why and the fix. 🔴 Red before: the
    goal was handed to browse's model."""
    from personalclaw.action_providers.browse_provider import BrowseActionProvider

    result = await BrowseActionProvider().execute(
        {"goal": "sign in with {{secret:ORCHARD_KEY}}", "start_url": START},
        types.SimpleNamespace(event="t", payload={}),
        timeout=5,
    )

    err = result.agent_error
    assert err.what == "browse's goal can't name a secret"
    assert err.why == (
        "the goal is what browse's model reads, and a secret's value never reaches a model"
    )
    assert err.fix == (
        "take the {{secret:…}} out of the goal: to sign in, let the run pause and sign in yourself"
    )


# ── the tab browse leaves behind ──────────────────────────────────────────────────────────────


async def _real_closer(monkeypatch: pytest.MonkeyPatch) -> Any:
    from test_browse_cdp_preflight import _ClosableFake

    from personalclaw.action_providers.browse_provider import BrowseActionProvider
    from personalclaw.browse import transport as transport_mod

    fake = _ClosableFake()

    async def _connect(*_a: Any, **_k: Any) -> Any:
        return fake

    monkeypatch.setattr(transport_mod.WebSocketCdpTransport, "connect", staticmethod(_connect))
    session, _driver, close = await BrowseActionProvider()._open(
        {}, types.SimpleNamespace(event="t"), cdp_url="ws://fake/1"
    )
    await session.start()
    fake.sent.clear()
    return fake, close


def _left(fake: Any) -> tuple[bool, bool]:
    """Whether the tab was left blank, and whether its back/forward list was reset."""
    from personalclaw.browse import cdp

    blanked = any(params == {"url": cdp.BLANK_URL} for _m, params in fake.sent)
    return blanked, any(method == getattr(cdp, "_RESET_HISTORY") for method, _p in fake.sent)


def _parked(reason: str) -> BrowseLoopResult:
    return BrowseLoopResult(ok=True, goal="g", final_url=SITE, parked=True, park_reason=reason)


@pytest.mark.asyncio
@pytest.mark.parametrize("ended", ["finished", "failed", "stuck", "after-a-hand-over"])
async def test_a_held_runs_tab_is_left_blank_however_it_ends(monkeypatch, ended):
    """The provider's own closer leaves the tab blank with its back/forward list reset after a
    held run, however it ended, the run after a hand-over included. 🔴 Red before: nothing pinned
    the closer, nor the two commands it sends."""
    from personalclaw.browse.loop import PARK_STUCK

    fake, close = await _real_closer(monkeypatch)
    result = {
        "finished": BrowseLoopResult(ok=True, goal="g", final_url=SITE),
        "failed": None,
        "stuck": _parked(PARK_STUCK),
        "after-a-hand-over": None,
    }[ended]

    with filled_secrets.handed([VALUE]):
        await close(result)

    assert _left(fake) == (True, True)


@pytest.mark.asyncio
@pytest.mark.parametrize("park", ["login_required", "human_challenge", "taken_over"])
async def test_a_park_that_hands_the_tab_to_a_person_leaves_its_page(monkeypatch, park):
    """A sign-in, a challenge and a take-over leave the page for the person they hand it to.
    🔴 Red before: the tab was blanked under them."""
    fake, close = await _real_closer(monkeypatch)

    with filled_secrets.handed([VALUE]):
        await close(_parked(park))

    assert _left(fake) == (False, False)


@pytest.mark.asyncio
async def test_a_run_that_held_nothing_leaves_its_tab_as_it_is(monkeypatch):
    fake, close = await _real_closer(monkeypatch)

    await close(BrowseLoopResult(ok=True, goal="g", final_url=SITE))

    assert _left(fake) == (False, False)


@pytest.mark.asyncio
async def test_the_closer_never_raises_when_the_tab_is_gone(monkeypatch):
    fake, close = await _real_closer(monkeypatch)
    closed: list[bool] = []

    async def _gone(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("the tab is gone")

    async def _close() -> None:
        closed.append(True)

    monkeypatch.setattr(fake, "send", _gone)
    monkeypatch.setattr(fake, "close", _close)

    with filled_secrets.handed([VALUE]):
        await close(None)

    assert closed == [True], "the socket still closes"


# ── a held page's link destinations, and addresses a browser reads loosely ──────────────────

#: A value whose address a page carries on encoded twice, as `encodeURIComponent(location.href)`
#: writes it: the mask doesn't know that form, so only showing no link destination keeps it out.
LINKED = "Lk4m Pz8q&Wy3v"


def _linking_page() -> str:
    """A page whose links carry its own address on, as a sign-in link's return address does: a
    titled link, an escaped parenthesised path, a spaced address and an autolink."""
    here = SITE + "docs?k=" + LINKED.replace(" ", "%20")
    back = quote(here, safe="-_.!~*'()")
    autolink = f"https://login.example.test/r?return={back}"
    return (
        "<html><body><p>Docs</p><form><input name='q'></form>"
        f"<a href='https://login.example.test/signin?return={back}' title='Sign in'>Sign in</a>"
        f"<a href='{SITE}wiki/A_(PATHMARK)?return={back}'>Wiki</a>"
        f"<a href='https://login.example.test/a b?return={back}'>Spaced</a>"
        f"<a href='{autolink}'>{autolink}</a>"
        "</body></html>"
    )


def _linked_forms() -> list[str]:
    once = quote(LINKED.replace(" ", "%20"), safe="-_.!~*'()")
    return [once, "PATHMARK", *(p for p in re.split(r"[^A-Za-z0-9]+", LINKED) if len(p) >= 4)]


class _Guarded:
    """Browse's model behind the model-call guard: it keeps what passed the guard."""

    def __init__(self, *replies: str) -> None:
        from test_browse_is_never_handed_a_secrets_value import _Model

        from personalclaw.guardrails.model_call import ModelCallGuard

        self.model = _Model()
        self.guard = ModelCallGuard(self.model, use_case="browse", provider_name="o", model="m")
        self.replies = list(replies)

    async def __call__(self, prompt: str) -> str:
        async for _ in self.guard.stream(prompt):
            pass
        return self.replies.pop(0) if self.replies else "DONE"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["perceive", "verify"])
async def test_a_held_pages_link_destinations_reach_no_prompt(path):
    """Its text shows each link as its words alone, on the page a step reads and on the one a
    submission is verified on, through the guard. These destinations run past 100 characters, so
    the Links DSL leaves them out (``extraction._clean_link``); a short one is the next pin's.
    🔴 Red before: a titled, parenthesised, spaced or autolinked destination was shown whole in the
    page's text, the value in it."""
    page = _FakePage({START: _linking_page()}, url="")
    if path == "verify":
        page.replace(START, "<html><body><form><input name='q'></form></body></html>")
        landing = SITE + "welcome"
        page.replace(landing, _linking_page())

        def _submitted(p: _FakePage) -> None:
            p.url = landing

        page.on_submit = _submitted
    decide = _Guarded("SUBMIT", "FORM_OK", "DONE") if path == "verify" else _Guarded("DONE")

    with filled_secrets.handed([VALUE, LINKED]):
        await run_browse_loop(
            goal="read", start_url=START, session=_FakeSession(page), page=page, decide=decide
        )

    sent = decide.model.sent
    assert sent and any("Docs" in prompt for prompt in sent), "vacuity: the page was read"
    assert [f for f in _linked_forms() if any(f in prompt for prompt in sent)] == []


def _worded() -> str:
    """An address that carries the page's own address on, as ``encodeURIComponent(location.href)``
    writes it."""
    here = SITE + "docs?k=" + LINKED.replace(" ", "%20")
    return "https://login.example.test/r?return=" + quote(here, safe="-_.!~*'()")


def _worded_page() -> str:
    """A page whose short links and controls are worded as an address that carries the page's own
    address on, as ``encodeURIComponent(location.href)`` writes it: a link's words, a link with a
    title alone, a button's words, an input's placeholder (with a name, and with none, so it is
    its label too) and an input's type."""
    worded = _worded()
    return (
        f"<html><body><p>Docs</p><form><input name='q'><button>{worded}</button>"
        f"<input name='p' placeholder='{worded}'><input placeholder='{worded}'>"
        f"<input name='r' type='{worded}'></form>"
        f"<a href='/signin'>{worded}</a><a href='/help' title='{worded}'></a></body></html>"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["perceive", "verify"])
async def test_a_held_pages_words_worded_as_an_address_reach_no_prompt(path):
    """A short link worded as an address, one with a title alone, a button worded so and an input
    whose placeholder or type is, are shown with each address in their words withheld (a site the
    run doesn't know), on the page a step reads and on the one a submission is verified on, through
    the guard; the value is in no prompt in any case. 🔴 Red before: the Links DSL and the elements
    showed their words, or an input's note, as the page wrote them, the value in them."""
    page = _FakePage({START: _worded_page()}, url="")
    if path == "verify":
        page.replace(START, "<html><body><form><input name='q'></form></body></html>")
        landing = SITE + "welcome"
        page.replace(landing, _worded_page())

        def _submitted(p: _FakePage) -> None:
            p.url = landing

        page.on_submit = _submitted
    decide = _Guarded("SUBMIT", "FORM_OK", "DONE") if path == "verify" else _Guarded("DONE")

    with filled_secrets.handed([VALUE, LINKED]):
        await run_browse_loop(
            goal="read", start_url=START, session=_FakeSession(page), page=page, decide=decide
        )

    sent = decide.model.sent
    held = [f for f in _linked_forms() if any(f.lower() in prompt.lower() for prompt in sent)]
    assert held == [], held
    shown = [prompt for prompt in sent if "] [withheld] → https://orchard.example.test/" in prompt]
    assert shown, "vacuity: the short links were listed"
    for words in (
        "button: [withheld]",
        'placeholder="[withheld]"',
        "type=[withheld]",
        "field: [withheld] placeholder=",
    ):
        assert all(words in prompt for prompt in shown), words


def test_the_forms_dsl_shows_a_held_pages_words_as_origins():
    """The Forms DSL shows a form's name and each field's label, state and note with each address
    in them withheld, a site the run doesn't know. 🔴 Red before: it showed them as the page wrote
    them."""
    from personalclaw.browse.extraction import extract_page, render_forms_dsl

    named = _worded_page().replace("<form>", f"<form name='{_worded()}'>", 1)
    forms = extract_page(named, url=START).forms

    with filled_secrets.handed([VALUE, LINKED]):
        shown = render_forms_dsl(forms)

    held = [f for f in _linked_forms() if f.lower() in shown.lower()]
    assert held == [], held
    assert 'placeholder="[withheld]"' in shown, shown
    assert '[form: "[withheld]"]' in shown, shown


#: Link targets a browser reads loosely, each holding markers past what its origin shows, and what
#: browse shows of each: its site's origin as a browser reads it (a site the run knows), or
#: nothing.
LOOSE = {
    "a-backslash": ("https://x.example.test\\PATHMARK\\QMARK", "https://x.example.test/"),
    "a-percent-in-its-host": ("https://x.example.test%PATHMARK/QMARK", "[withheld]"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("href", sorted(LOOSE))
async def test_a_loosely_written_link_shows_its_origin_or_nothing(href):
    """🔴 Red before: path text was shown as the host."""
    written, shown = LOOSE[href]
    html = f"<html><body><p>Docs</p><a href='{written}'>Back</a></body></html>"
    page = _FakePage({START: html}, url="")
    decide = _Decide("DONE")

    await _held_run(page, decide, knows=("x.example.test",))

    (prompt,) = decide.page_prompts
    assert f"Back → {shown}" in prompt and _found(prompt) == [], prompt


# ── the closer, through execute ──────────────────────────────────────────────────────────────

#: How a held run ends, and whether its tab is left blank with its back/forward list reset.
ENDINGS = {
    "a-sign-in-hands-it-over": ("login_required", False),
    "a-challenge-hands-it-over": ("human_challenge", False),
    "a-take-over-hands-it-over": ("taken_over", False),
    "the-run-after-finishes": ("finished", True),
    "the-run-after-fails": ("failed", True),
    "the-run-after-raises": ("raised", True),
    "the-run-after-is-cancelled": ("cancelled", True),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", sorted(ENDINGS))
async def test_execute_hands_its_closer_the_loops_result(monkeypatch, ending):
    """The provider's closer is handed what the loop returned: a park that hands the tab to a
    person keeps the page, and the run after it (its answer given) leaves the tab blank however
    it ends. 🔴 Red before, at this commit's parent: no run left its tab blank, so the four runs
    after a hand-over failed, and the three hand-overs passed. A closer handed nothing
    (``closer(None)``) blanks a hand-over's page, which those three catch."""
    from test_browse_cdp_preflight import _ClosableFake

    from personalclaw.action_providers import browse_provider as bp
    from personalclaw.browse import transport as transport_mod

    how, blanked = ENDINGS[ending]
    fake = _ClosableFake()
    ran: list[str] = []

    async def _connect(*_a: Any, **_k: Any) -> Any:
        return fake

    async def _loop(**kw: Any) -> BrowseLoopResult:
        fake.sent.clear()
        ran.append(how)
        if how == "raised":
            raise RuntimeError("the page broke")
        if how == "cancelled":
            raise asyncio.CancelledError
        if how in ("finished", "failed"):
            return BrowseLoopResult(ok=how == "finished", goal=kw["goal"], final_url=SITE)
        return BrowseLoopResult(
            ok=True, goal=kw["goal"], final_url=SITE, parked=True, park_reason=how
        )

    monkeypatch.setattr(transport_mod.WebSocketCdpTransport, "connect", staticmethod(_connect))
    monkeypatch.setattr(bp, "run_browse_loop", _loop)
    answer = "signed in" if blanked else None
    ctx = types.SimpleNamespace(event="workflow_step", payload={}, answer=answer)

    with filled_secrets.handed([VALUE]):
        try:
            await bp.BrowseActionProvider().execute(
                {"goal": "read the docs", "start_url": START, "cdp_url": "ws://fake/1"},
                ctx,
                timeout=5,
            )
        except (asyncio.CancelledError, RuntimeError):
            pass

    assert ran == [how], "vacuity: the loop ran"
    assert _left(fake) == (blanked, blanked), (ending, fake.sent)


#: Held values an image's ``alt`` holds with a quote of the other kind, or a ``>``: the page's
#: markup as a browser serializes it (a ``'`` and a ``>`` kept as they are in a ``"``-quoted value).
ALT_VALUES = {"a-quote": "Tr0ub4dor'x7Kq9vB", "a-greater-than-sign": "Tr0ub4dor>x7Kq9vB"}


@pytest.mark.asyncio
@pytest.mark.parametrize("held", sorted(ALT_VALUES))
async def test_an_images_words_holding_a_held_value_are_masked_whole(held):
    """An image's words in a page's text are read whole, whatever quote or ``>`` they hold, so a
    held value in them is masked whole. 🔴 Red before: the words were cut at it."""
    value = ALT_VALUES[held]
    html = f'<html><body><p>hi <img alt="{value}" src="x.png"> there</p></body></html>'
    page = _FakePage({START: html}, url="")
    decide = _Decide("DONE")

    with filled_secrets.handed([value]):
        await run_browse_loop(
            goal="read", start_url=START, session=_FakeSession(page), page=page, decide=decide
        )

    (prompt,) = decide.page_prompts
    assert _shown_pieces(value, prompt) == [], prompt
    assert "hi [IMAGE: [REDACTED: credential]] there" in prompt, "vacuity: the image was read"


def test_a_controls_ref_says_nothing_of_a_held_value_in_its_words():
    """While a held value is in a link's or a button's words, its ref is made from those words as
    they are shown, so two values give the same ref. 🔴 Red before: the ref was a hash of the
    value."""
    from personalclaw.browse.extraction import extract_page

    refs = []
    for pin in ("4417", "9023"):
        html = f"<html><body><a href='/k'>{pin}</a><button>{pin}</button></body></html>"
        with filled_secrets.handed([pin]):
            page = extract_page(html, url=START)
        refs.append(
            ([link.ref for link in page.links], [f.ref for form in page.forms for f in form.fields])
        )
    assert refs[0][0] and refs[0] == refs[1], refs


#: A value with a space, a backtick, an apostrophe, ``?``, ``#`` and braces, and how a browser
#: writes it into a fragment, recorded from ``new URL(…).hash`` (the WHATWG URL Standard): only the
#: space and the backtick encoded, where a path or a query would encode more.
BROWSER_FRAGMENT = ("bv 7q'1x?z`9{k}2#W", "bv%207q'1x?z%609{k}2#W")


def test_a_value_is_masked_as_a_browser_writes_it_into_a_fragment():
    """🔴 Red before: a browser's fragment form was kept as it was."""
    value, written = BROWSER_FRAGMENT

    assert (
        filled_secrets.masked(f"at /p#{written} now", [value]) == f"at /p#{filled_secrets.MASK} now"
    )


# ── what a held run names, by the sites it knows ──────────────────────────────────────────────


def _real_session(resolver: Any) -> Any:
    """A gated session over a fake transport whose guard is the real one, *resolver* its DNS."""
    from test_browse_cdp_preflight import FakeTransport, _inline

    from personalclaw.browse import cdp

    transport = FakeTransport()
    return transport, cdp.GatedCdpSession(transport, resolver=resolver, blocking=_inline)


async def _framed(transport: Any, session: Any, url: str) -> None:
    """*url* reported as a frame of the page, and its refusal waited for."""
    from personalclaw.browse import cdp

    await transport.listener(
        cdp.FRAME_NAVIGATED, {"frame": {"id": "F1", "parentId": "TOP", "url": url}}
    )
    for _ in range(100):
        if session.blocks:
            return
        await asyncio.sleep(0.01)


#: A frame on a host a held value maps to, refused for where it resolves: (held, the frame as
#: Node's WHATWG URL writes it, the address the fake resolver answers).
RESOLVED = {
    "an-ipv4-mapped-host": ("::ffff:10.0.0.5", "https://[::ffff:a00:5]/y", "::ffff:10.0.0.5"),
    "a-name-resolving-to-a-held-number": (
        "10.0.0.5#x7Kq9vB",
        "https://mirror.example.test/y",
        "10.0.0.5",
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("held", sorted(RESOLVED))
async def test_a_refusal_for_where_a_host_resolves_names_no_address(monkeypatch, held):
    """A frame refused because its host resolves to a private address is told, and audited, in
    fixed words that name neither the host nor the address it resolved to. 🔴 Red before: the
    guard's own sentence was said, the resolved address in it."""
    value, frame, answer = RESOLVED[held]
    rows = _Rows(monkeypatch)
    with credentials.known_sites(START), filled_secrets.handed([value]):
        transport, session = _real_session(lambda _host: [answer])
        await session.start()
        await _framed(transport, session, frame)

    (block,) = session.blocks
    reasons = [block.reason, *block.recovery_hints, *(row["reason"] for row in rows.rows)]
    assert block.reason == "host [withheld] is on a private network", block.reason
    assert block.recovery_hints == [
        "Fetch a public URL.",
        "To see which host it was, run the step once without its secret.",
        "If it is yours, add it to Allowed hosts in Settings → Security → Network egress.",
    ], block.recovery_hints
    assert [row["reason"] for row in rows.rows] == [block.reason], rows.rows
    assert not any("10.0.0.5" in each for each in reasons), reasons


@pytest.mark.asyncio
async def test_a_short_host_off_the_allow_list_is_refused_in_whole_words(monkeypatch):
    """A one-letter host refused on an allow-list is told, and audited, in fixed words with the
    host withheld in their one slot, no letter of them cut out. 🔴 Red before: the host's letter
    was cut out of the guard's words wherever it stood in them."""
    _listed(monkeypatch)
    rows = _Rows(monkeypatch)
    with credentials.known_sites(START), filled_secrets.handed(["a#x7Kq9vBzz"]):
        transport, session = _real_session(lambda _host: ["93.184.216.40"])
        await session.start()
        await _framed(transport, session, "https://a/")

    (block,) = session.blocks
    assert block.reason == "host [withheld] is not on this run's egress allow-list", block.reason
    assert block.recovery_hints == [
        "This run's safety profile limits egress to an allow-list.",
        "To see which host it was, run the step once without its secret.",
        "To allow it, add that host to Allowed hosts in Settings → Security → Network egress, or "
        "widen the egress tier in the governance ceiling.",
    ], block.recovery_hints
    assert [row["reason"] for row in rows.rows] == [block.reason], rows.rows
    assert not any("[withheld]llow" in each for each in (block.reason, *block.recovery_hints))


@pytest.mark.asyncio
async def test_a_refusal_of_the_start_site_names_it(monkeypatch):
    """A refusal of the run's own start site names it, in the same fixed words, and its hint names
    the step that lets it through. The family's control: a refusal that names no site at all would
    pass the rows above."""
    _listed(monkeypatch)
    rows = _Rows(monkeypatch)
    with credentials.known_sites(START), filled_secrets.handed([VALUE]):
        transport, session = _real_session(lambda _host: ["93.184.216.40"])
        await session.start()
        await _framed(transport, session, f"{SITE}x")

    (block,) = session.blocks
    assert block.host == "orchard.example.test", block
    assert block.reason == "host orchard.example.test is not on this run's egress allow-list"
    assert block.recovery_hints == [
        "This run's safety profile limits egress to an allow-list.",
        "To allow it, add orchard.example.test to Allowed hosts in Settings → Security → Network "
        "egress, or widen the egress tier in the governance ceiling.",
    ], block.recovery_hints
    assert [row["host"] for row in rows.rows] == ["orchard.example.test"], rows.rows


#: Words a page writes naming a host IDNA writes for a held value: with a mark or a sign around
#: it, the hosts as Node's WHATWG URL writes them.
ACE_WORDS = {
    "in-brackets": "(xn--mnchen12-65a.xn--caf-dma.test)",
    "in-quotes": "'xn--mnchen12-65a.xn--caf-dma.test'",
    "after-an-equals-sign": "a=xn--mnchen12-65a.xn--caf-dma.test",
    "in-backticks": "`xn--mnchen12-65a`",
    "before-an-ampersand": "xn--mnchen12-65a&x=1",
    "before-an-asterisk": "xn--mnchen12-65a*",
    "after-a-known-host": "orchard.example.test/xn--mnchen12-65a",
}


@pytest.mark.parametrize("words", sorted(ACE_WORDS))
def test_a_held_values_idna_host_in_words_is_withheld_whole(words):
    """A host IDNA writes for a held value, in a page's or a record's words, is withheld whole,
    whatever marks are around it. 🔴 Red before: it was shown."""
    from personalclaw.browse import cdp

    text = ACE_WORDS[words]
    with credentials.known_sites(START), filled_secrets.handed(["Munchen12"]):
        shown = [
            credentials.screen_text(text),
            credentials.masked_in_addresses(text),
            cdp._held_masked(f"the frame could not be read ({text})"),
        ]

    assert not any("xn--mnchen12" in each.lower() for each in shown), shown
    for each in shown[:2]:
        assert "[withheld]" in each and filled_secrets.MASK not in each, shown


def test_a_held_value_and_its_idna_host_are_masked_and_withheld_apart():
    """In one line of words, a held value as written is masked and the host IDNA writes for it is
    withheld. 🔴 Red before: the host was shown."""
    with credentials.known_sites(START), filled_secrets.handed(["Munchen12"]):
        shown = credentials.masked_in_addresses("Munchen12 at (xn--mnchen12-65a.xn--caf-dma.test)")

    assert shown == "[REDACTED: credential] at [withheld]", shown


def test_a_known_sites_idna_host_in_words_is_kept():
    """A site the run knows, written as IDNA writes it, is kept in a page's and a record's words,
    and its address reads as its origin. The family's control."""
    from personalclaw.browse import cdp

    words = "see (xn--bcher-kva.test) now"
    with credentials.known_sites("https://xn--bcher-kva.test/"), filled_secrets.handed([VALUE]):
        assert credentials.screen_text(words) == words
        assert credentials.masked_in_addresses(words) == words
        assert cdp._held_masked(words) == words
        assert credentials.screen_text("see https://xn--bcher-kva.test/x now") == (
            "see https://xn--bcher-kva.test/ now"
        )


def test_a_host_idna_cannot_read_is_withheld_and_never_raises():
    """A host whose ``xn--`` label no decoder can read is withheld, and words naming it are read
    without raising. 🔴 Red before: reading the words raised."""
    host = "xn--99999999999999999999999a.example.test"
    with credentials.known_sites(START), filled_secrets.handed(["Tr0ub4dorQx"]):
        assert credentials.shown_host(f"https://{host}/", host) == "[withheld]"
        assert "xn--9999" not in credentials.masked_in_addresses(f"see {host} now")
        assert "xn--9999" not in credentials.screen_text(f"see https://{host}/x now")


@pytest.mark.parametrize("name", sorted(LONG_RUNS))
def test_a_long_run_of_text_is_masked_at_once_while_a_value_is_held(name):
    """Reading a held page's text for its addresses and the hosts IDNA writes takes time in step
    with its length. Green before too."""
    import cpu_budget

    run = LONG_RUNS[name]
    with credentials.known_sites(START), filled_secrets.handed([VALUE]):
        took = cpu_budget.seconds(lambda: credentials.screen_text(run))
    assert took < cpu_budget.limit(1.0), f"{took:.2f}s"


@pytest.mark.asyncio
async def test_a_held_run_sent_to_two_withheld_sites_is_not_told_it_visited_them():
    """Two sites the run doesn't know both show withheld, so the second is no page it already
    visited. Green before too, where they showed apart."""
    html = "<html><body><p>Version 2.0</p></body></html>"
    away = ["https://mirror.example.test/a", "https://other.example.test/b"]
    page = _FakePage({START: html, **{url: html for url in away}}, url="")
    decide = _Decide(*(_to(url) for url in away), "DONE")

    await _held_run(page, decide)

    assert len(decide.page_prompts) == 3, "vacuity: each page was read"
    assert not any("already visited" in prompt for prompt in decide.prompts), decide.prompts


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["perceive", "verify"])
async def test_a_page_on_a_withheld_site_has_its_own_host_masked_in_its_words(path):
    """A page on a site the run doesn't know is read with its own host, and each long label of it,
    masked, on the page a step reads and on the one a submission is verified on: a page can write
    its host into its words, and a held value with it. A link back to the start site still names
    it. 🔴 Red before: the page's words named its host."""
    away = "https://x7kq9vb.evil.test/landing"
    html = (
        "<html><body><p>You are on x7kq9vb.evil.test today, at x7kq9vb</p>"
        f"<a href='{SITE}docs'>Back</a></body></html>"
    )

    class _Elsewhere(_FakePage):
        async def current_url(self) -> str:
            return away if path == "perceive" or self.url == away else self.url

    page = _Elsewhere({START: html}, url="")
    if path == "verify":
        page.replace(START, "<html><body><form><input name='q'></form></body></html>")
        page.replace(away, html)

        def _submitted(p: _FakePage) -> None:
            p.url = away

        page.on_submit = _submitted
    decide = _Decide("SUBMIT", "FORM_OK", "DONE") if path == "verify" else _Decide("DONE")

    with filled_secrets.handed(["Tr0ub4dor@x7Kq9vB"]):
        await run_browse_loop(
            goal="read", start_url=START, session=_FakeSession(page), page=page, decide=decide
        )

    read = [prompt for prompt in decide.prompts if "You are on" in prompt]
    assert read, "vacuity: the page was read"
    assert not any("x7kq9vb" in prompt.lower() for prompt in read), read
    assert all("Back → https://orchard.example.test/" in prompt for prompt in read), read


@pytest.mark.asyncio
async def test_a_watched_pages_tick_knows_its_page():
    """A watched page's tick knows its page before its session opens, so the page is named while
    a value is held, as the session is opened and when it is opened to. A guard: green before too,
    where no table existed."""
    from personalclaw.browse.plan_runner import make_content_tick_runner
    from personalclaw.browse.plans import KIND_WATCH_PAGE, BrowsePlan

    seen: list[str] = []
    page = _FakePage({START: "<html><body><main><p>Docs here</p></main></body></html>"}, url="")

    class _Seeing(_FakeSession):
        async def navigate(self, url: str) -> Any:
            seen.append(credentials.origin_of(url))
            return await super().navigate(url)

    async def _open(_cdp_url: str) -> Any:
        async def _close() -> None:
            return None

        seen.append(credentials.origin_of(START))
        return _Seeing(page), page, _close

    runner = make_content_tick_runner(open_session=_open, resolve_url=lambda: "ws://fake/1")
    plan = BrowsePlan(id="p1", goal="watch the docs", kind=KIND_WATCH_PAGE, start_url=START)
    with filled_secrets.handed([VALUE]):
        await runner(plan)

    assert seen == [SITE, SITE], seen


@pytest.mark.asyncio
async def test_a_browse_step_knows_its_sites_before_the_browser_is_touched(monkeypatch):
    """A browse step knows its start page's site, and each host its egress allow-list names, before
    its browser's transport starts the tasks it reads events in, so a frame those tasks refuse
    names it. 🔴 Red before: the refusal was not said in fixed words. A guard for the order: a step
    that knew its sites only once its session was open would withhold the frame's host."""
    from test_browse_cdp_preflight import _ClosableFake

    from personalclaw.action_providers import browse_provider as bp
    from personalclaw.browse import cdp
    from personalclaw.browse import transport as transport_mod
    from personalclaw.net.guard import GuardDecision

    fake = _ClosableFake()
    reading: list[asyncio.Task[Any]] = []

    async def _reported() -> None:
        while fake.listener is None:
            await asyncio.sleep(0)
        for fid, url in (("F1", f"{SITE}x"), ("F2", "https://docs.example.test/x")):
            frame = {"id": fid, "parentId": "TOP", "url": url}
            await fake.listener(cdp.FRAME_NAVIGATED, {"frame": frame})

    async def _connect(*_a: Any, **_k: Any) -> Any:
        reading.append(asyncio.get_running_loop().create_task(_reported()))
        return fake

    def _refused(url: str, _policy: Any, **_k: Any) -> GuardDecision:
        host = _browser_host(url)
        return GuardDecision(
            allow=False,
            url=url,
            host=host,
            reason=f"host {host!r} is not resolvable",
            category="unresolvable",
        )

    blocks: list[Any] = []

    async def _loop(**kw: Any) -> BrowseLoopResult:
        await kw["session"].start()
        await asyncio.gather(*reading)
        blocks.extend(kw["session"].blocks)
        return BrowseLoopResult(ok=True, goal=kw["goal"], final_url=SITE)

    monkeypatch.setattr(transport_mod.WebSocketCdpTransport, "connect", staticmethod(_connect))
    monkeypatch.setattr(cdp, "evaluate", _refused)
    monkeypatch.setattr(bp, "run_browse_loop", _loop)
    monkeypatch.setattr(bp, "_listed_hosts", lambda: ("docs.example.test",), raising=False)
    ctx = types.SimpleNamespace(event="workflow_step", payload={}, answer="signed in")

    with filled_secrets.handed([VALUE]):
        await bp.BrowseActionProvider().execute(
            {"goal": "read the docs", "start_url": START, "cdp_url": "ws://fake/1"}, ctx, timeout=5
        )

    assert [b.host for b in blocks] == ["orchard.example.test", "docs.example.test"], blocks
    assert blocks[0].reason == "host orchard.example.test could not be found", blocks[0].reason


def test_only_the_hosts_an_allow_list_names_exactly_are_known():
    """An egress allow-list's entries are known as the hosts they name: not a subdomain of one,
    and not an entry with a wildcard. 🔴 Red before: every host was shown."""
    listed = ("docs.example.test", "example.test", "*.cdn.example.test")
    with credentials.known_sites(START, listed), filled_secrets.handed([VALUE]):
        assert credentials.origin_of("https://docs.example.test/x") == "https://docs.example.test/"
        assert credentials.origin_of("https://evil.example.test/") == "[withheld]"
        assert credentials.origin_of("https://a.cdn.example.test/") == "[withheld]"


def test_a_value_that_joins_the_hold_later_withholds_a_site_shown_before():
    """A site is weighed against the values held when it is shown, so a value that joins the hold
    later withholds it, and the site is shown again once that value is let go. 🔴 Red before: the
    site was shown with the value masked in it."""
    with credentials.known_sites(START), filled_secrets.handed([VALUE]):
        assert credentials.origin_of(START) == SITE
        with filled_secrets.handed(["orchard"]):
            assert credentials.origin_of(START) == "[withheld]"
        assert credentials.origin_of(START) == SITE


def test_a_scheme_a_held_value_names_withholds_its_addresses():
    """A known site's address is withheld while a held value holds its scheme's word as a piece.
    🔴 Red before: it was shown."""
    with credentials.known_sites(START), filled_secrets.handed(["https#Kx7q9"]):
        assert credentials.origin_of(START) == "[withheld]"


@pytest.mark.asyncio
async def test_a_quarantine_says_nothing_an_exception_said_while_a_value_is_held():
    """A session quarantined because the browser refused a command says why in browse's own
    words, without what the exception said, while a value is held. 🔴 Red before: the
    exception's text was kept, an address in it."""
    from test_browse_cdp_preflight import FakeTransport

    from personalclaw.browse import cdp

    class _Refusing(FakeTransport):
        async def send(self, method: str, params: dict | None = None, **kw: Any) -> dict:
            if method == cdp.ADD_SCRIPT:
                raise RuntimeError("the page at https://mirror.example.test/ refused it")
            return await super().send(method, params, **kw)

    session = cdp.GatedCdpSession(_Refusing(), resolver=lambda _h: ["93.184.216.40"])
    with credentials.known_sites(START), filled_secrets.handed([VALUE]):
        with pytest.raises(cdp.CdpSessionError) as refused:
            await session.start()

    assert "guard script was not installed" in str(refused.value), "vacuity: it said why"
    assert "mirror" not in str(refused.value) and "mirror" not in session.quarantine_reason


def _signed_in_to(held: str) -> Any:
    """The hand-over a run starting at :data:`START` is parked on to sign in, while *held* is
    held."""
    from personalclaw.browse.handoff import REASON_NO_SESSION, request_login

    with credentials.known_sites(START), filled_secrets.handed([held]):
        return request_login(START, reason=REASON_NO_SESSION)


def test_a_sign_in_never_names_a_start_site_a_held_value_coincides_with():
    """The sign-in hand-over names the start site as browse shows it: withheld, with no profile
    named, while a held value coincides with it. 🔴 Red before: it named the site."""
    handoff = _signed_in_to(COINCIDING["a-label"])

    assert handoff.site == "[withheld]", handoff.site
    assert "orchard" not in handoff.sentence and "orchard" not in json.dumps(handoff.to_payload())


def test_a_sign_in_names_the_start_site_while_a_value_is_held():
    """The sign-in hand-over names the start site while a held value doesn't coincide with it.
    The family's control."""
    handoff = _signed_in_to(VALUE)

    assert handoff.site == "orchard.example.test" and "orchard.example.test" in handoff.sentence
    assert handoff.item["evidence"]["profile"].endswith("orchard.example.test"), handoff.item


async def _granted(monkeypatch: pytest.MonkeyPatch, held: str) -> list[tuple[str, ...]]:
    """The scope the grant a run in your own browser asks for names, while *held* is held: its
    request is stubbed to record it, then stop the run."""
    from personalclaw.action_providers import browse_provider as bp
    from personalclaw.browse import grant as grant_mod
    from personalclaw.browse import target as target_mod

    asked: list[tuple[str, ...]] = []

    class _Asked(Exception):
        pass

    async def _request(**kw: Any) -> Any:
        asked.append(tuple(kw["scope"]))
        raise _Asked

    status = types.SimpleNamespace(connected=True, device_id="d1", reason="", fix="")
    monkeypatch.setattr(target_mod, "connector_status", lambda: status)
    monkeypatch.setattr(grant_mod, "grant_gate", lambda: None)
    monkeypatch.setattr(grant_mod, "request_grant", _request)
    ctx = types.SimpleNamespace(event="workflow_step", payload={}, answer=None)
    config = {"goal": "read the docs", "start_url": START, "target": "user_browser"}

    with filled_secrets.handed([held]), pytest.raises(_Asked):
        await bp.BrowseActionProvider().execute(config, ctx, timeout=5)
    return asked


@pytest.mark.asyncio
async def test_a_grant_never_names_a_start_site_a_held_value_coincides_with(monkeypatch):
    """The grant a run in your own browser asks for names the start site as browse shows it:
    withheld while a held value coincides with it. 🔴 Red before: the scope it asked for named the
    site."""
    asked = await _granted(monkeypatch, COINCIDING["a-label"])

    assert asked == [("[withheld]",)], asked


@pytest.mark.asyncio
async def test_a_grant_names_the_start_site_while_a_value_is_held(monkeypatch):
    """The grant names the start site while a held value doesn't coincide with it. The family's
    control."""
    asked = await _granted(monkeypatch, VALUE)

    assert asked == [("orchard.example.test",)], asked
