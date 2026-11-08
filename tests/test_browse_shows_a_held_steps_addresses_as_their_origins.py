"""While a browse step holds a stored secret's value, every address it shows is its origin.

Its prompts, its links and the words a page wrote for them and its controls, the lines its steps
record, its notes, its parks, its live view and its audit rows show each address as
``scheme://host[:port]/`` alone (an address with no host as its scheme, or ``[withheld]``), masked
of the value before anything keeps, relays or cuts them. A park that hands the tab to a person
leaves the page for them; any other end leaves the tab blank.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import types
from dataclasses import asdict
from typing import Any
from urllib.parse import quote

import pytest
from test_browse_loop_and_provider import _Decide, _FakePage, _FakeSession

from personalclaw import filled_secrets
from personalclaw.browse import sentinels
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
    blob = "\n".join(t if isinstance(t, str) else json.dumps(t, default=str) for t in texts)
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
    """One browse loop run while its work holds :data:`VALUE`, and what its live view was sent."""
    frames: list[dict] = []
    with filled_secrets.handed([VALUE]):
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

    with filled_secrets.handed([VALUE]):
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
    text and its Links DSL, and a link labelled with its own address is labelled with that origin.
    🔴 Red before: a relative target, the text's own link addresses, and a label that was its
    link's address, were shown whole."""
    html = (
        "<html><body><p>Docs</p>"
        "<a href='/account/PATHMARK-7q1x/settings#tab=WQXZT'>Account</a>"
        "<a href='//other.example.test/deep/PLMKV'>Other</a>"
        "<a href='https://abs.example.test/deep/ABSPATH-9z/x?q=QMARK#FRAGM'></a>"
        "</body></html>"
    )
    page = _FakePage({START: html}, url="")
    decide = _Decide("DONE")

    await _held_run(page, decide)

    (prompt,) = decide.page_prompts
    assert "→ https://orchard.example.test/" in prompt
    assert "→ https://other.example.test/" in prompt
    assert "https://abs.example.test/ → https://abs.example.test/" in prompt
    assert _found(decide.prompts) == []


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
LONG_RUNS = {"letters": "x" * 200_000, "letters-and-dots": "a." * 100_000, "digits": "1a" * 100_000}


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
        assert addresses_as_origins("x_mailto:a@b.test?body=QMARK") == "x_mailto:"


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
    assert sent and any("Sign in" in prompt for prompt in sent), "vacuity: the page was read"
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
    whose placeholder or type is, are shown with each address in their words as its origin, on the
    page a step reads and on the one a submission is verified on, through the guard; the value is
    in no prompt in any case. 🔴 Red before: the Links DSL and the elements showed their words, or
    an input's note, as the page wrote them, the value in them."""
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
    shown = [prompt for prompt in sent if "https://login.example.test/ → " in prompt]
    assert shown, "vacuity: the short links were listed"
    for words in (
        "button: https://login.example.test/",
        'placeholder="https://login.example.test/"',
        "type=https://login.example.test/",
        "field: https://login.example.test/ placeholder=",
    ):
        assert all(words in prompt for prompt in shown), words


def test_the_forms_dsl_shows_a_held_pages_words_as_origins():
    """The Forms DSL shows a form's name and each field's label, state and note with each address
    in them as its origin. 🔴 Red before: it showed them as the page wrote them."""
    from personalclaw.browse.extraction import extract_page, render_forms_dsl

    named = _worded_page().replace("<form>", f"<form name='{_worded()}'>", 1)
    forms = extract_page(named, url=START).forms

    with filled_secrets.handed([VALUE, LINKED]):
        shown = render_forms_dsl(forms)

    held = [f for f in _linked_forms() if f.lower() in shown.lower()]
    assert held == [], held
    assert 'placeholder="https://login.example.test/"' in shown, shown
    assert '[form: "https://login.example.test/"]' in shown, shown


#: Link targets a browser reads loosely, each holding markers past what its origin shows, and what
#: browse shows of each: its site's origin as a browser reads it, or nothing.
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

    await _held_run(page, decide)

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
