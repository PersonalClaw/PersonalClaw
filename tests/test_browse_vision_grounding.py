"""BA-10 — the located vision-grounding fallback for the browse loop.

The atom's subject is a page shape the DOM cannot describe: a ``<canvas>`` or image-map whose only
interactive control has no addressable element, so ``extract_page`` yields **zero** refs and every
ref-addressed action the model can emit names nothing. This file drives that fixture through
``run_browse_loop`` and asserts the four rails that define correctness.

**The fixture's own premise is asserted first**
(:func:`test_the_canvas_fixture_exposes_no_addressable_element`), with an ordinary page as the
positive control. Without that pair every assertion below could be passing on a fixture that simply
never reached the vision path — the vacuity this atom is most exposed to, because "no refs" is also
what a broken extractor returns.

**Rail (a) — no new vendor string, no new model socket.**
:func:`test_the_only_resolution_seam_is_the_existing_image_modality_use_case` reads the module's
source and asserts the one resolution call is ``provider_bridge``'s, and that no vendor name appears
anywhere in the BA-10 surface. A provider-agnostic core is not provable by reading the happy path —
a bespoke ``httpx.post`` to ``localhost:11434`` would satisfy every behavioural test in this file.

**Rail (b) — honest refusal.** With nothing bound to ``image_modality`` the canvas fixture PARKS
with a typed reason, and the park detail names a model to pull. The negative control that makes this
mean something is :func:`test_a_bound_vision_model_grounds_and_clicks_the_canvas`: the same fixture,
one binding, and the click happens — so the park is caused by the absent model and not by a path
that never works.

**Rail (c) — no bundle, no size regression.** Asserted against **OU-14's own** licence rail
(``personalclaw.bundled_model``) rather than a second copy of its rules, which is what keeps this
file from contradicting the lane that owns that budget: BA-10 adds no weight, declares no
``size_budget_bytes``, and consumes ``licence_decision`` read-only to prove every model it
recommends is one C9 already permits.

**The soul guardrail.** A CAPTCHA *is* a canvas with no addressable element — the exact page that
reaches this path — so ``CLICK_VISION`` on a human-verification page must refuse, and refuse before
an image is sent anywhere. :func:`test_a_human_challenge_is_refused_before_any_model_is_resolved`
proves the ordering by failing the resolver outright: if the refusal came second, the test would see
the resolver's exception instead of the refusal.

**The C9 licence trap.** ``qwen2.5vl:3b`` is ``qwen-research`` — non-commercial — and one digit from
the model we do recommend. :func:`test_nothing_in_the_tree_recommends_the_non_commercial_qwen_3b`
greps the whole repository, because the defect this prevents is a doc or a config example naming it,
not a line of Python.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from personalclaw.browse import vision
from personalclaw.browse.extraction import extract_page
from personalclaw.browse.loop import (
    PARK_HUMAN_CHALLENGE,
    PARK_VISION_UNAVAILABLE,
    SEL_OPERATION_VISION_CLICK,
    action_vocabulary,
    run_browse_loop,
)
from personalclaw.browse.page import DISPATCH_MOUSE_EVENT, CdpPageDriver, PageActionError
from personalclaw.browse.sentinels import ClickAction, ClickVisionAction, parse_sentinel

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: The atom's fixture: the sole interactive control is drawn on a canvas, plus an image-map for good
#: measure. Neither yields an ``ElementRef`` — asserted, not assumed, in the first test.
CANVAS_PAGE = """<!doctype html><html><head><title>Seat map</title></head><body>
<h1>Pick your seat</h1>
<p>The seat map is drawn below. Click the seat you want.</p>
<canvas id="seatmap" width="800" height="600"></canvas>
<img src="/plan.png" alt="floor plan" usemap="#plan">
<map name="plan"><area shape="rect" coords="10,10,60,60" alt="Aisle A"></map>
</body></html>"""

#: The positive control for the fixture's premise — an ordinary page whose controls DO have refs.
LINKED_PAGE = """<!doctype html><html><head><title>Home</title></head><body>
<h1>Home</h1><a href="/next">Next page</a>
<form name="f"><input type="text" name="q"><button type="submit">Go</button></form>
</body></html>"""

#: A CAPTCHA: a canvas with no addressable control AND a human-verification challenge. The shape
#: that makes the guardrail load-bearing rather than decorative.
CHALLENGE_PAGE = """<!doctype html><html><head><title>Check</title></head><body>
<h1>Security check</h1>
<p>Please verify you are human before continuing.</p>
<canvas id="challenge" width="300" height="80"></canvas>
</body></html>"""


# ── test doubles ──────────────────────────────────────────────────────────────────────────────


class _Session:
    """A ``GatedCdpSession`` stand-in that allows every navigation and records it."""

    def __init__(self) -> None:
        self.navigated: list[str] = []

    async def start(self) -> None:
        return None

    async def navigate(self, url: str) -> Any:
        self.navigated.append(url)
        return type("Nav", (), {"ok": True, "reason": ""})()


class _Page:
    """A ``PageDriver`` over one HTML string that records every actuation, refused ones included."""

    def __init__(self, html: str, *, screenshot: str = "", viewport=(1000.0, 800.0)) -> None:
        self._html = html
        self._screenshot = screenshot
        self._viewport = viewport
        self.clicks: list[Any] = []
        self.coordinate_clicks: list[tuple[float, float]] = []
        self.fills: list[Any] = []

    async def html(self) -> str:
        return self._html

    async def current_url(self) -> str:
        return "https://example.test/seats"

    async def click(self, ref) -> None:
        self.clicks.append(ref)

    async def click_at(self, x: float, y: float) -> None:
        self.coordinate_clicks.append((x, y))

    async def viewport(self) -> tuple[float, float]:
        return self._viewport

    async def fill(self, ref, value: str) -> None:
        self.fills.append((ref, value))

    async def submit(self) -> None:
        return None

    async def scroll(self, direction: str) -> None:
        return None

    async def go_back(self) -> None:
        return None

    async def screenshot(self) -> str:
        return self._screenshot


def _decider(*replies: str):
    """A ``Decide`` that answers the given lines in order, then DONE."""
    queue = list(replies)

    async def _decide(_prompt: str) -> str:
        return queue.pop(0) if queue else "DONE"

    return _decide


def _shot(tmp_path: Path) -> str:
    """A real 1x1 PNG on disk — a real file, so ``image_data_url`` is exercised, not stubbed."""
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00"
        b"\x00\x00IEND\xaeB`\x82"
    )
    path = tmp_path / "step.png"
    path.write_bytes(png)
    return str(path)


class _SelSpy:
    """Captures ``log_api_access`` kwargs so the audit row can be asserted on, not inferred."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def log_api_access(self, **kwargs: Any) -> None:
        self.rows.append(kwargs)


@pytest.fixture()
def sel_spy(monkeypatch: pytest.MonkeyPatch) -> _SelSpy:
    spy = _SelSpy()
    import personalclaw.sel as sel_module

    monkeypatch.setattr(sel_module, "sel", lambda: spy)
    return spy


def _bind_vision(monkeypatch: pytest.MonkeyPatch, reply: str) -> list[str]:
    """Bind a fake vision model to the ``image_modality`` seam. Returns the prompts it saw.

    Patches the two ``provider_bridge`` entry points :mod:`browse.vision` imports lazily — the same
    two functions the real path calls — so the test exercises everything from ``ground`` down
    including the content-block shape, while resolving no real provider.
    """
    seen: list[str] = []

    class _Event:
        def __init__(self, text: str) -> None:
            from personalclaw.llm.base import EVENT_TEXT_CHUNK

            self.kind = EVENT_TEXT_CHUNK
            self.text = text

    class _Provider:
        async def complete(self, messages, **_kwargs):
            blocks = messages[0]["content"]
            seen.append(json.dumps(blocks)[:4000])
            yield _Event(reply)

    import personalclaw.providers.provider_bridge as bridge

    monkeypatch.setattr(bridge, "can_resolve_use_case", lambda uc: uc == vision.VISION_USE_CASE)
    monkeypatch.setattr(bridge, "resolve_provider_for_use_case", lambda uc, **_k: _Provider())
    return seen


async def _run(page: _Page, decide, *, vision_grounding: bool):
    return await run_browse_loop(
        goal="book seat 14",
        start_url="https://example.test/seats",
        session=_Session(),
        page=page,
        decide=decide,
        max_steps=3,
        vision_grounding=vision_grounding,
    )


# ── the fixture's own premise, with its positive control ───────────────────────────────────────


def test_the_canvas_fixture_exposes_no_addressable_element() -> None:
    """The atom's precondition, MEASURED — and the control that proves the measurement works.

    "Zero refs" is both the fixture's defining property and what a broken extractor returns for
    every page, so the ordinary page is asserted in the same test. Without it, an extraction
    regression would make this whole file pass while testing nothing.
    """
    canvas = extract_page(CANVAS_PAGE, url="https://example.test/seats")
    addressable = len(canvas.links) + sum(len(f.fields) for f in canvas.forms)
    assert addressable == 0, f"the canvas fixture exposes {addressable} addressable element(s)"

    control = extract_page(LINKED_PAGE, url="https://example.test/home")
    control_refs = len(control.links) + sum(len(f.fields) for f in control.forms)
    assert control_refs >= 2, (
        "the ORDINARY page also yielded no refs, so the canvas result above is an extraction "
        f"failure rather than a property of the fixture (found {control_refs})"
    )


# ── "named explicitly, never auto-selected" — both halves ─────────────────────────────────────


def test_the_vocabulary_withholds_the_verb_unless_the_run_opted_in() -> None:
    """Half one of the discipline: an action the prompt never advertises is never reached for."""
    assert "CLICK_VISION" not in action_vocabulary()
    assert "CLICK_VISION" not in action_vocabulary(vision_grounding=False)
    assert "CLICK_VISION" in action_vocabulary(vision_grounding=True)


@pytest.mark.asyncio
async def test_a_disabled_run_refuses_the_verb_even_when_the_model_emits_it(tmp_path: Path) -> None:
    """Half two: the EXECUTOR refuses, so the withheld prompt line is not the only guard.

    A prompt is a request. This is the single call site of ``click_at``, and it is where BA-4 places
    its credential refusal for the same reason — a rule stated only in the prompt is one a model
    talks itself out of, and a rule in the caller is bypassed by the next caller.
    """
    page = _Page(CANVAS_PAGE, screenshot=_shot(tmp_path))
    result = await _run(page, _decider("CLICK_VISION the seat in row 4"), vision_grounding=False)

    assert page.coordinate_clicks == [], "a disabled run dispatched a coordinate click"
    assert not result.parked, result.park_reason
    assert any("not enabled" in s.note for s in result.steps), [s.note for s in result.steps]


@pytest.mark.asyncio
async def test_a_failed_ref_click_never_falls_back_to_a_coordinate(tmp_path: Path) -> None:
    """The desktop rule, held: "no fallback from ``auto`` onto a coordinate method".

    Enabled run, and the model emits an ordinary ``CLICK`` naming a ref that is not on the page.
    The loop must warn and leave the coordinate path untouched — an automatic widening here would
    fire precisely when the model is most confused about the page.
    """
    page = _Page(CANVAS_PAGE, screenshot=_shot(tmp_path))
    result = await _run(page, _decider("CLICK deadbeef"), vision_grounding=True)

    assert page.coordinate_clicks == [], "a failed ref lookup was widened into a coordinate click"
    assert any("unknown ref" in s.note for s in result.steps), [s.note for s in result.steps]


@pytest.mark.asyncio
async def test_the_vision_path_refuses_a_page_that_has_refs(tmp_path: Path) -> None:
    """And it cannot REPLACE the ref path either — the rule points both ways.

    On a page with addressable elements a coordinate click is a worse way to do something that
    already works, and permitting it is how the located path becomes the model's default.
    """
    page = _Page(LINKED_PAGE, screenshot=_shot(tmp_path))
    result = await _run(page, _decider("CLICK_VISION the Next link"), vision_grounding=True)

    assert page.coordinate_clicks == []
    assert any("addressable" in s.note for s in result.steps), [s.note for s in result.steps]


# ── rail (b): the honest refusal, and the control that gives it meaning ────────────────────────


@pytest.mark.asyncio
async def test_with_no_vision_model_bound_the_canvas_fixture_parks_with_a_typed_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rail (b). The atom's word is "never a silent no-op", so three things are asserted:
    it parks, the reason is the TYPED constant, and the detail is actionable."""
    import personalclaw.providers.provider_bridge as bridge

    monkeypatch.setattr(bridge, "can_resolve_use_case", lambda _uc: False)
    page = _Page(CANVAS_PAGE, screenshot=_shot(tmp_path))
    result = await _run(page, _decider("CLICK_VISION the seat in row 4"), vision_grounding=True)

    assert result.parked, "the run did not park — this is the silent no-op the atom forbids"
    assert result.park_reason == PARK_VISION_UNAVAILABLE, result.park_reason
    assert vision.REASON_NO_VISION_MODEL in result.park_detail, result.park_detail
    assert page.coordinate_clicks == []
    # Actionable: the park names a model to pull and the licence it comes under.
    assert "qwen2.5vl:7b" in result.park_detail, result.park_detail
    assert "Apache-2.0" in result.park_detail, result.park_detail
    # A park keeps the run's work (`_park`'s own contract) rather than reporting failure.
    assert result.ok is True


@pytest.mark.asyncio
async def test_a_bound_vision_model_grounds_and_clicks_the_canvas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The atom's central clause, and the NEGATIVE CONTROL for the park above.

    Same fixture, one binding: the loop grounds on the screenshot and actuates a coordinate. Without
    this arm the park test could be passing against a path that never works at all.

    The scaling is asserted numerically: 0.5/0.25 of a 1000x800 CSS viewport is (500, 200). A
    fraction-to-pixel bug is the most likely defect here and it would otherwise look like bad
    grounding forever.
    """
    seen = _bind_vision(monkeypatch, "POINT 0.5 0.25")
    page = _Page(CANVAS_PAGE, screenshot=_shot(tmp_path), viewport=(1000.0, 800.0))
    result = await _run(page, _decider("CLICK_VISION the seat in row 4"), vision_grounding=True)

    assert page.coordinate_clicks == [(500.0, 200.0)], page.coordinate_clicks
    assert not result.parked, f"{result.park_reason}: {result.park_detail}"
    assert any("vision grounding" in s.note for s in result.steps), [s.note for s in result.steps]

    # The call really carried the image as a content block, and really said the image is data.
    assert len(seen) == 1, seen
    assert '"type": "image_url"' in seen[0]
    assert "data:image/png;base64," in seen[0]
    assert "untrusted" in seen[0].lower()
    assert "the seat in row 4" in seen[0]


@pytest.mark.asyncio
async def test_a_model_that_cannot_find_the_target_warns_instead_of_clicking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``NOT_FOUND`` is a first-class answer: no click, no park, and the agent may try again."""
    _bind_vision(monkeypatch, "NOT_FOUND")
    page = _Page(CANVAS_PAGE, screenshot=_shot(tmp_path))
    result = await _run(
        page, _decider("CLICK_VISION a seat that is not drawn"), vision_grounding=True
    )

    assert page.coordinate_clicks == []
    assert not result.parked, result.park_reason


@pytest.mark.asyncio
async def test_a_step_with_no_screenshot_refuses_rather_than_grounding_on_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``screenshot()`` answers "" when capture is unavailable; grounding then has no subject."""
    _bind_vision(monkeypatch, "POINT 0.5 0.5")
    page = _Page(CANVAS_PAGE, screenshot="")
    result = await _run(page, _decider("CLICK_VISION the seat"), vision_grounding=True)

    assert page.coordinate_clicks == []
    assert any("screenshot" in s.note for s in result.steps), [s.note for s in result.steps]


@pytest.mark.asyncio
async def test_a_zero_viewport_refuses_rather_than_clicking_the_top_left_corner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every fraction scales to (0, 0) on a zero viewport — a click on whatever sits in the corner,
    reported as a success. Refused instead, because that is a defect wearing a green result."""
    _bind_vision(monkeypatch, "POINT 0.5 0.5")
    page = _Page(CANVAS_PAGE, screenshot=_shot(tmp_path), viewport=(0.0, 0.0))
    result = await _run(page, _decider("CLICK_VISION the seat"), vision_grounding=True)

    assert page.coordinate_clicks == [], "a coordinate was actuated against a zero viewport"
    assert any("viewport" in s.note for s in result.steps), [s.note for s in result.steps]


# ── the soul guardrail ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_human_challenge_is_refused_before_any_model_is_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 THE SOUL GUARDRAIL. A CAPTCHA is a canvas with no ref — this path's own fixture shape.

    The ORDERING is the assertion. Both ``provider_bridge`` entry points are replaced with functions
    that raise, so if the refusal came after resolution the run would carry the resolver's error
    instead of the refusal. A guard that refuses only after sending the image has already sent it.
    """

    def _explode(*_a: Any, **_k: Any):
        raise AssertionError("a model was resolved for a human-verification page")

    import personalclaw.providers.provider_bridge as bridge

    monkeypatch.setattr(bridge, "can_resolve_use_case", _explode)
    monkeypatch.setattr(bridge, "resolve_provider_for_use_case", _explode)

    # The DESCRIPTION is deliberately innocent ("the checkbox"), so what refuses is the screen over
    # the PAGE. That is the arm that matters: a hostile page is the threat model, and an agent that
    # described the control neutrally would defeat a request-only screen.
    page = _Page(CHALLENGE_PAGE, screenshot=_shot(tmp_path))
    result = await _run(page, _decider("CLICK_VISION the checkbox"), vision_grounding=True)

    assert page.coordinate_clicks == [], "a coordinate was clicked on a human-verification page"
    assert result.parked and result.park_reason == PARK_HUMAN_CHALLENGE, result.park_reason
    assert "verify you are human" in result.park_detail, result.park_detail


def test_the_challenge_screen_reads_the_page_as_well_as_the_request() -> None:
    """Either side can be the tell, so both are screened — and the refusal NAMES what matched."""
    assert vision.human_challenge(page_text="Security check: reCAPTCHA", description="the box")
    assert vision.human_challenge(page_text="Sign in", description="the I'm not a robot checkbox")
    assert vision.human_challenge(page_text="Enter the verification code", description="field")
    # The control arm: an ordinary page and an ordinary request are NOT refused, so the screen is
    # a screen rather than a blanket refusal that would make every assertion above vacuous.
    assert vision.human_challenge(page_text="Pick your seat", description="seat 14") == ""


def test_there_is_no_coordinate_TYPE_anywhere_in_the_located_path() -> None:
    """credential-never-transits, structurally.

    BA-4's invariant rests on ``extraction`` classifying a field as a credential and the loop
    refusing to ``fill`` it. A coordinate the agent could TYPE into would sidestep that
    classification entirely — the field would never have been examined. So the located path is
    CLICK-only by construction, and this asserts that construction rather than trusting a
    docstring: the driver protocol grows exactly one coordinate verb, and it is a click.
    """
    from personalclaw.browse import loop as loop_module

    protocol = ast.parse(Path(loop_module.__file__).read_text(encoding="utf-8"))
    driver = next(
        node
        for node in ast.walk(protocol)
        if isinstance(node, ast.ClassDef) and node.name == "PageDriver"
    )
    methods = {n.name for n in driver.body if isinstance(n, ast.AsyncFunctionDef)}
    assert "click_at" in methods, methods
    coordinate_writers = {m for m in methods if m.endswith("_at")}
    assert coordinate_writers == {"click_at"}, (
        f"the driver grew a second coordinate verb: {sorted(coordinate_writers)}. A coordinate "
        "TYPE would put a value into a field the extraction never classified as a credential."
    )
    assert not hasattr(CdpPageDriver, "fill_at")
    assert not hasattr(CdpPageDriver, "type_at")


# ── the SEL audit, as its own operation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_located_click_is_audited_under_its_own_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sel_spy: _SelSpy
) -> None:
    """Mirrors ``computer_click:located`` — a coordinate click is ONE FILTER away from every other.

    Sharing the ordinary operation string would make the located path invisible in the only place
    anyone can later ask "did this run click something the DOM never described".
    """
    _bind_vision(monkeypatch, "POINT 0.25 0.5")
    page = _Page(CANVAS_PAGE, screenshot=_shot(tmp_path), viewport=(800.0, 600.0))
    await _run(page, _decider("CLICK_VISION the seat in row 4"), vision_grounding=True)

    rows = [r for r in sel_spy.rows if r.get("operation") == SEL_OPERATION_VISION_CLICK]
    assert rows, f"no {SEL_OPERATION_VISION_CLICK} row was written; saw {sel_spy.rows}"
    row = rows[-1]
    assert row["outcome"] == vision.OUTCOME_GROUNDED, row
    assert row["source"] == "browse"
    payload = json.loads(row["resources"])
    assert payload["x"] == 200.0 and payload["y"] == 300.0, payload
    # Distinct from the park operation, which is the point of a separate string.
    assert SEL_OPERATION_VISION_CLICK != "browse.park"


@pytest.mark.asyncio
async def test_a_refused_located_click_is_audited_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sel_spy: _SelSpy
) -> None:
    """The guardrail must be VISIBLE. Auditing only the successes would hide every refusal, which
    is the half of the trail an auditor actually needs."""
    import personalclaw.providers.provider_bridge as bridge

    monkeypatch.setattr(bridge, "can_resolve_use_case", lambda _uc: False)
    page = _Page(CANVAS_PAGE, screenshot=_shot(tmp_path))
    await _run(page, _decider("CLICK_VISION the seat"), vision_grounding=True)

    rows = [r for r in sel_spy.rows if r.get("operation") == SEL_OPERATION_VISION_CLICK]
    assert [r["outcome"] for r in rows] == [vision.OUTCOME_NO_MODEL], rows


def test_every_outcome_word_reads_the_way_this_module_says_it_does() -> None:
    """🔴 The CAPTCHA refusal must be findable by an operator scanning for refusals.

    ``sel.audit_outcome_tone`` is the server's own stamper, so this asserts how each word ACTUALLY
    renders rather than re-deriving the matcher. Pinned per word because the drift that motivated
    ``AUDIT_OUTCOME_FAMILIES`` was a single security-relevant word rendering neutral grey, and
    ``refused_challenge`` is exactly that shape: the guardrail's own audit row.

    The neutral three are neutral ON PURPOSE (see the constants' comment): nothing was denied to a
    caller and nothing broke, so the precedent the table sets for ``expired``/``halted_on_budget``
    applies. They are asserted here so the choice is a recorded decision, not an accident.
    """
    from personalclaw.sel import audit_outcome_tone

    assert audit_outcome_tone(vision.OUTCOME_REFUSED_CHALLENGE) == "danger", (
        "the CAPTCHA/2FA refusal does not read as a refusal on the audit surface — an operator "
        "filtering for denials would not find the one row that matters most here"
    )
    assert audit_outcome_tone("refused_not_opted_in") == "danger"
    assert audit_outcome_tone("refused_has_refs") == "danger"
    assert audit_outcome_tone(vision.OUTCOME_NOT_FOUND) == "danger"
    assert audit_outcome_tone(vision.OUTCOME_FAILED) == "danger"
    for neutral in (
        vision.OUTCOME_GROUNDED,
        vision.OUTCOME_NO_MODEL,
        vision.OUTCOME_NO_SCREENSHOT,
    ):
        assert audit_outcome_tone(neutral) == "neutral", neutral


def test_every_outcome_literal_this_module_writes_has_exactly_one_family() -> None:
    """The ceiling rail (``test_audit_outcome_families.py``) counts ``outcome="LITERAL"`` writes.

    Asserted locally so a future edit to this file reds here, next to the code, rather than as a
    count in a shared rail that names no owner. **Exactly one** family, checked in both directions,
    because each miss is a different defect and this change hit the second one:

    * **Zero** families → the word renders neutral, understating a refusal, and it raises the shared
      ceiling.
    * **Two** families → two pills both claim the row and lie about each other. Measured: the first
      spelling of the opt-in refusal, ``refused_not_enabled``, was claimed by ``denied`` (via
      ``refused``) *and* ``ok`` (via ``enabled``) at once — a composite carrying one token from each
      of two families. ``disabled`` has the same problem. So an outcome word must avoid every OTHER
      family's vocabulary, not merely include its own.
    """
    from personalclaw.browse import loop as loop_module
    from personalclaw.sel import AUDIT_OUTCOME_FAMILIES, _outcome_token_match

    source = Path(loop_module.__file__).read_text(encoding="utf-8")
    literals = set(re.findall(r'outcome="([a-z_]+)"', source))
    assert literals, "no outcome literals found in browse/loop.py — the scan is vacuous"
    for word in literals:
        owners = [
            f["key"]
            for f in AUDIT_OUTCOME_FAMILIES
            if any(_outcome_token_match(str(v), word) for v in f["values"])
        ]
        assert len(owners) == 1, (
            f"browse/loop.py writes outcome={word!r}, claimed by {owners or 'NO family'}. It needs "
            "exactly one: give it a family-joining prefix (e.g. `refused_`) that collides with no "
            "other family's vocabulary, or classify it in sel.py. Zero families renders neutral "
            "and raises the shared ceiling rail; two makes both pills lie about each other."
        )


def test_every_park_reason_renders_as_a_sentence_not_a_reason_code() -> None:
    """``_park_sentence`` calls itself "the exhaustive projection of the park vocabulary" and warns
    that "a park reason with no sentence is a leaked identifier on a product surface". It was not
    exhaustive: BA-10's two new reasons fell through to the ``else`` and a user would have read
    *"Browse stopped early (vision_unavailable)"*.

    So this iterates the WHOLE vocabulary — discovered from the module's own ``PARK_*`` constants
    rather than listed here, because a hand-written list is what let two reasons slip through the
    existing login-only test. A lane that adds a park reason and no sentence reds here.
    """
    from personalclaw.action_providers.browse_provider import BrowseActionProvider
    from personalclaw.browse import loop as loop_module
    from personalclaw.browse.handoff import PARK_LOGIN_REQUIRED
    from personalclaw.browse.loop import BrowseLoopResult

    reasons = {
        value
        for name, value in vars(loop_module).items()
        if name.startswith("PARK_") and isinstance(value, str)
    } | {PARK_LOGIN_REQUIRED}
    assert len(reasons) >= 8, f"only {len(reasons)} park reasons discovered; the scan broke"

    for reason in sorted(reasons):
        result = BrowseLoopResult(
            ok=True,
            goal="g",
            parked=True,
            park_reason=reason,
            park_detail="d",
            final_url="https://example.test/x",
        )
        sentence = BrowseActionProvider._park_sentence(result)
        assert reason not in sentence, (
            f"the park sentence for {reason!r} leaks the reason CODE to a user: {sentence!r}. "
            "Give it a sentence in `_park_sentence` — its docstring calls itself the exhaustive "
            "projection of the park vocabulary, and the `else` branch prints the raw identifier."
        )
        assert (
            "stopped early" not in sentence
        ), f"{reason!r} fell through to the generic `else` branch: {sentence!r}"


# ── rail (a): no new vendor string, no new model socket ────────────────────────────────────────


def test_the_only_resolution_seam_is_the_existing_image_modality_use_case() -> None:
    """Rail (a), on the SOURCE — because the happy path cannot show what is absent.

    A bespoke ``httpx.post`` to ``localhost:11434`` would satisfy every behavioural test above. So
    this asserts the shape: the module resolves through ``provider_bridge``, names the existing
    capability, and contains no vendor identifier and no socket of its own.
    """
    source = Path(vision.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert {"resolve_provider_for_use_case", "can_resolve_use_case"} <= imported, imported
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert "personalclaw.providers.provider_bridge" in modules, modules

    assert vision.VISION_USE_CASE == "image_modality", vision.VISION_USE_CASE

    # Asserted on the CODE, not the raw file. The module's own prose says "no app-owned model
    # socket" and names Ollama as the provider a user pulls from, so a substring scan over the whole
    # text fails on the sentences that promise the very property under test. Imports answer "does it
    # open a connection of its own" exactly; string literals answer "does it name an endpoint".
    imported_modules = {
        (node.module or "") for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    for forbidden in ("httpx", "requests", "aiohttp", "urllib", "socket", "http.client"):
        assert not any(
            mod == forbidden or mod.startswith(f"{forbidden}.") for mod in imported_modules
        ), (
            f"browse/vision.py imports {forbidden!r} — the vision fallback must resolve ONLY "
            "through the existing image_modality capability, not a socket of its own"
        )

    # No endpoint and no vendor string in any literal the code actually uses. Docstrings are
    # excluded because they are where the module explains the constraint.
    # `clean=False` is load-bearing: `get_docstring` runs `inspect.cleandoc` by default, so the
    # cleaned text never equals the raw `ast.Constant` it came from and every docstring would fall
    # through into the scan below as if it were code.
    docstrings = {
        ast.get_docstring(node, clean=False)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docstrings
    ]
    for literal in literals:
        low = literal.lower()
        for forbidden in ("11434", "/api/chat", "/api/generate", "http://", "https://"):
            assert forbidden not in low, (
                f"browse/vision.py's literal {literal[:80]!r} names {forbidden!r} — it must "
                "reach a model only through the provider bridge"
            )
        # `ollama pull …` is a user-facing INSTRUCTION for an already-registered provider app, not a
        # core vendor branch, so it is the one vendor word allowed and only inside `obtain`/`note`.
        for vendor in ("openai", "anthropic", "bedrock", "gemini", "mistral", "llama.cpp"):
            assert vendor not in low, f"browse/vision.py's code names the vendor {vendor!r}"

    # And there is no vendor BRANCH: the module never compares against a provider name.
    assert "ollama" not in {
        n.id.lower() for n in ast.walk(tree) if isinstance(n, ast.Name)
    }, "browse/vision.py has an identifier named for a vendor"


def test_the_recommended_refs_are_model_references_not_paths() -> None:
    """A recommendation the wheel could CARRY would defeat rail (c) by another door."""
    assert vision.DEFAULT_MODEL_REF == "ollama:qwen2.5vl:7b", vision.DEFAULT_MODEL_REF
    for model in vision.RECOMMENDED_MODELS:
        assert not model.model_id.startswith(("/", ".", "~")), model
        assert not Path(model.model_id).is_absolute(), model
        for suffix in (".gguf", ".onnx", ".safetensors", ".bin", ".pt"):
            assert suffix not in model.model_id.lower(), model


# ── rail (c): no bundle, no size regression — asserted against OU-14's OWN rail ────────────────


def test_every_recommended_model_is_permissively_licensed_by_ou14s_own_rail() -> None:
    """Rail (c) + the C9 licence constraint, enforced by the rail that OWNS it.

    ``licence_decision`` is imported from ``personalclaw.bundled_model`` and used READ-ONLY. That
    is deliberate and it is what keeps this file from colliding with the lane that owns OU-14's
    budget: BA-10 adds no ceiling, changes no allowlist, and simply asks the existing gate whether
    each licence it recommends is one C9 already permits. If OU-14 widens or narrows the allowlist,
    this test follows it instead of contradicting it.
    """
    from personalclaw.bundled_model import PERMITTED_LICENCES, licence_decision

    assert vision.RECOMMENDED_MODELS, "the recommendation list is empty — nothing to license-check"
    for model in vision.RECOMMENDED_MODELS:
        decision = licence_decision(model.licence)
        assert decision.permitted, (
            f"BA-10 recommends {model.model_id!r} under {model.licence!r}, which OU-14's licence "
            f"rail refuses: {decision.reason}"
        )
        assert decision.identifier in PERMITTED_LICENCES, decision


def test_ba10_adds_no_model_artifact_to_the_shipped_tree() -> None:
    """Rail (c) proper: the model is user-PULLED, so BA-10 ships no weight.

    Measured with OU-14's own ``weight_shaped_members`` over the packaged source tree, so "what
    counts as a weight" is its definition rather than a second list that could drift. This cannot
    trip OU-14's size budget because it adds nothing to weigh — and it deliberately does NOT assert
    ``repo_declaration() is None``, which is OU-14's own state to change.
    """
    from personalclaw.bundled_model import weight_shaped_members

    pkg = _REPO_ROOT / "src" / "personalclaw"
    names = [str(p.relative_to(_REPO_ROOT)) for p in pkg.rglob("*") if p.is_file()]
    assert names, "no packaged files were found — the scan is vacuous"
    assert weight_shaped_members(names) == [], (
        "a model weight is present in the packaged source tree; BA-10's vision model is "
        "user-pulled and must add no artifact to the wheel or image"
    )


def test_ba10_declares_no_size_budget_of_its_own() -> None:
    """A second budget would be the contradiction the atom warns about (research §4: "no OU-14
    coupling"). BA-10 owns no ceiling because it ships nothing to measure."""
    source = Path(vision.__file__).read_text(encoding="utf-8")
    for token in ("size_budget", "SIZE_BUDGET", "BundleDeclaration", "gate_wheel"):
        assert token not in source, (
            f"browse/vision.py names {token!r} — BA-10 must not declare or enforce a packaging "
            "budget; that is OU-14's and a second one would contradict it"
        )


# ── the C9 licence trap ───────────────────────────────────────────────────────────────────────


def test_nothing_in_the_tree_recommends_the_non_commercial_qwen_3b() -> None:
    """🔴 THE LICENCE TRAP. ``qwen2.5vl:3b`` is ``qwen-research`` — NON-COMMERCIAL.

    A repository-wide grep, not a Python-only one, because the defect this prevents is a doc, a
    default, a config example or a test fixture naming it — recommending a non-commercial model in a
    permissively-licensed product is a real licensing defect, and it is the smaller, more tempting
    pull one digit from the model we do recommend.

    The positive control is asserted in the same test: the 7b we DO recommend must be found, so a
    grep that silently matched nothing cannot read as a pass.
    """
    if shutil.which("git") is None:  # pragma: no cover - git is present in CI and dev
        pytest.skip("git is unavailable, so a tracked-file grep cannot run")

    def _grep(pattern: str) -> list[str]:
        # `--untracked` is load-bearing: git grep searches TRACKED files by default, so on the
        # branch that introduces this rail the files under test are invisible to it and BOTH the
        # control and the trap read as clean. A licence rail that cannot see a new file is no rail.
        proc = subprocess.run(
            ["git", "grep", "-In", "-F", "--untracked", "--", pattern],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
        )
        return [ln for ln in proc.stdout.splitlines() if ln.strip()]

    control = _grep("qwen2.5vl:7b")
    assert control, (
        "the grep found no mention of qwen2.5vl:7b either, so it is not working — the 3b result "
        "below would be a false zero"
    )

    #: The only files whose JOB is to name the 3b: the module that documents why it is excluded, and
    #: this rail. Kept explicit and tiny — a THIRD file naming it reds here, which is the point.
    documenting = {"src/personalclaw/browse/vision.py", f"tests/{Path(__file__).name}"}
    #: A mention is admissible only if the same line also says it is forbidden. So "never recommend
    #: qwen2.5vl:3b" passes and "model: qwen2.5vl:3b" does not — even inside the two files above.
    prohibitive = ("never", "not ", "absent", "non-commercial", "qwen-research", "must ", "forbid")

    # Assembled from parts so this rail's own search line does not contain the literal it hunts.
    # Spelling it inline made the detector match ITSELF, which would have needed a self-exemption —
    # and a detector that exempts its own file is one a real recommendation can hide in.
    forbidden = "qwen2.5vl" + ":" + "3" + "b"

    offending: list[str] = []
    for line in _grep(forbidden):
        path, _, text = line.partition(":")
        low = text.lower()
        if path in documenting and any(marker in low for marker in prohibitive):
            continue
        offending.append(line)

    assert offending == [], (
        "qwen2.5vl:3b is qwen-research licensed (NON-COMMERCIAL) and must never be recommended, "
        "defaulted to, or used as a fixture — C9 binds whatever PersonalClaw recommends, so a "
        "research-only default is a real licensing defect. Found:\n" + "\n".join(offending)
    )


def test_no_recommendation_surface_can_name_the_3b_at_all() -> None:
    """The structural half of the trap — unfakeable by prose.

    The grep above can be argued with (a line could claim to be prohibitive); this cannot. Every
    surface that a user or the loop could read a recommendation FROM is enumerated and checked.
    """
    surfaces = [vision.DEFAULT_MODEL_REF] + [
        part
        for model in vision.RECOMMENDED_MODELS
        for part in (model.model_id, model.obtain, model.note, model.licence)
    ]
    for surface in surfaces:
        assert (
            "3b" not in surface.lower()
        ), f"a recommendation surface names a 3b model: {surface!r}"
    assert all("7b" in m.model_id.lower() or "7B" in m.model_id for m in vision.RECOMMENDED_MODELS)


def test_ou14s_licence_rail_already_refuses_the_licence_the_3b_ships_under() -> None:
    """Why the grep above matters: the licence itself is already a known trap, so the only way it
    reaches a user is by NAMING the model rather than its licence."""
    from personalclaw.bundled_model import licence_decision

    decision = licence_decision("qwen-research")
    assert not decision.permitted, decision
    assert "qwen-research" in decision.reason, decision.reason


# ── the sentinel vocabulary ───────────────────────────────────────────────────────────────────


def test_the_new_verb_round_trips_and_cannot_collide_with_an_ordinary_click() -> None:
    """``parse_sentinel(a.render()) == a`` is the vocabulary's contract, and the two CLICK verbs
    must stay distinguishable — a description that parsed as a ref would click the wrong thing."""
    action = ClickVisionAction(description="the red seat in row 4")
    assert parse_sentinel(action.render()) == action
    assert parse_sentinel("CLICK_VISION the seat") == ClickVisionAction(description="the seat")
    assert parse_sentinel("CLICK a1b2c3d4") == ClickAction(ref="a1b2c3d4")
    # A hex-looking description still parses as the vision verb, not as a ref.
    assert parse_sentinel("CLICK_VISION deadbeef") == ClickVisionAction(description="deadbeef")
    assert (
        parse_sentinel("CLICK_VISION") is None
    ), "a bare verb with no description is not an action"


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("POINT 0.5 0.25", (0.5, 0.25)),
        ("POINT 0.5, 0.25", (0.5, 0.25)),
        ("point 0 0", (0.0, 0.0)),
        ("I think: POINT 0.9 0.1", (0.9, 0.1)),
        ("POINT 1.02 0.5", (1.0, 0.5)),  # clamped: a model naming the far edge is understood
    ],
)
def test_a_grounding_reply_is_parsed_into_a_normalised_point(reply: str, expected) -> None:
    point = vision.parse_point(reply)
    assert point is not None, reply
    assert (round(point.fx, 4), round(point.fy, 4)) == expected


@pytest.mark.parametrize("reply", ["NOT_FOUND", "", "POINT 412 300", "POINT 0.5", "no idea"])
def test_a_reply_that_is_not_a_normalised_point_is_rejected(reply: str) -> None:
    """``POINT 412 300`` is the important one: a PIXEL answer. Clamping it would silently click the
    bottom-right corner on every step and look like bad grounding forever."""
    assert vision.parse_point(reply) is None, reply


def test_a_point_scales_to_css_pixels() -> None:
    assert vision.GroundedPoint(fx=0.5, fy=0.25).to_viewport(1000, 800) == (500.0, 200.0)


# ── the located event on the real driver ──────────────────────────────────────────────────────


class _Transport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send(self, method: str, params: dict | None = None) -> dict:
        self.sent.append((method, dict(params or {})))
        if method == "Runtime.evaluate":
            return {"result": {"value": json.dumps([1024, 768])}}
        return {}


@pytest.mark.asyncio
async def test_the_driver_dispatches_a_real_located_mouse_event() -> None:
    """The atom names the mechanism: ``Input.dispatchMouseEvent``. Asserted on the WIRE.

    Three events in pointer order, because a canvas UI that reads ``mousemove`` before ``mousedown``
    is the common case and a press/release pair alone silently does nothing there.
    """
    transport = _Transport()
    driver = CdpPageDriver(transport)
    await driver.click_at(120.0, 340.0)

    events = [p for m, p in transport.sent if m == DISPATCH_MOUSE_EVENT]
    assert [e["type"] for e in events] == ["mouseMoved", "mousePressed", "mouseReleased"], events
    assert all(e["x"] == 120.0 and e["y"] == 340.0 for e in events), events
    assert events[1]["button"] == "left" and events[1]["clickCount"] == 1
    assert events[0]["button"] == "none"


@pytest.mark.asyncio
async def test_the_driver_reads_the_css_viewport() -> None:
    driver = CdpPageDriver(_Transport())
    assert await driver.viewport() == (1024.0, 768.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf"), "12", True, None])
async def test_the_driver_refuses_a_coordinate_it_cannot_post_honestly(bad) -> None:
    """CDP accepts an off-viewport point silently, so a scaling bug would become an event nobody
    can find. Refused at the driver instead."""
    transport = _Transport()
    with pytest.raises(PageActionError):
        await CdpPageDriver(transport).click_at(bad, 10.0)
    assert [m for m, _ in transport.sent if m == DISPATCH_MOUSE_EVENT] == []


# ── the provider wiring: a rail with no caller guards nothing ──────────────────────────────────


def test_the_action_provider_passes_the_flag_through_and_defaults_it_off() -> None:
    """The flag must reach ``run_browse_loop`` from ``action_config``, or the whole path is inert —
    the failure mode this repository keeps finding. Asserted on the AST of the call."""
    from personalclaw.action_providers import browse_provider

    tree = ast.parse(Path(browse_provider.__file__).read_text(encoding="utf-8"))
    call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_browse_loop"
    )
    kwargs = {kw.arg for kw in call.keywords}
    assert (
        "vision_grounding" in kwargs
    ), f"browse_provider never passes vision_grounding to run_browse_loop; it passes {kwargs}"
    # And the loop's own default is OFF, so a caller that says nothing gets nothing.
    import inspect

    assert inspect.signature(run_browse_loop).parameters["vision_grounding"].default is False


# ── the live leg: a REAL pulled vision model ───────────────────────────────────────────────────

_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
#: Only the models BA-10 recommends. A live leg that accepted any vision model could pass against
#: one this atom must not recommend, which would quietly launder the licence constraint.
_LIVE_MODELS = tuple(m.model_id for m in vision.RECOMMENDED_MODELS)


def _pulled_grounding_model() -> str:
    """A recommended vision model this host has actually pulled, or ``""``."""
    try:
        with urllib.request.urlopen(f"{_OLLAMA_HOST}/api/tags", timeout=3) as response:
            tags = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return ""
    names = {str(m.get("name") or "") for m in tags.get("models") or []}
    for wanted in _LIVE_MODELS:
        for name in names:
            if name == wanted or name.split(":")[0] == wanted.split(":")[0]:
                return name
    return ""


@pytest.mark.asyncio
async def test_a_real_pulled_vision_model_grounds_a_click_on_the_canvas_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The atom's end-to-end clause: a REAL pulled model, not a mocked ``decide``.

    Skipped when no recommended vision model is pulled on this host — the atom's own route is
    "user-PULLED", so a machine without one genuinely cannot run this leg. It is NOT skipped for
    convenience: the mocked arms above prove the plumbing, and only this one proves a real
    multimodal call reaches a local model through the ``image_modality`` seam and comes back as a
    coordinate the loop actuates.

    The assertion is deliberately weak on WHERE the model points (a 64x64 synthetic target is not a
    benchmark) and strong on the CHAIN: a real model produced a parseable normalised point, the loop
    scaled it into the viewport, and ``Input.dispatchMouseEvent`` carried it.
    """
    model = _pulled_grounding_model()
    if not model:
        pytest.skip(
            f"no recommended vision model pulled on {_OLLAMA_HOST} "
            f"(want one of {', '.join(_LIVE_MODELS)}: `{vision.RECOMMENDED_MODELS[0].obtain}`)"
        )

    provider = _live_provider(model)
    import personalclaw.providers.provider_bridge as bridge

    monkeypatch.setattr(bridge, "can_resolve_use_case", lambda uc: uc == vision.VISION_USE_CASE)
    monkeypatch.setattr(bridge, "resolve_provider_for_use_case", lambda uc, **_k: provider)

    page = _Page(CANVAS_PAGE, screenshot=_target_png(tmp_path), viewport=(800.0, 600.0))
    result = await _run(
        page,
        _decider("CLICK_VISION the solid red square"),
        vision_grounding=True,
    )

    assert not result.parked, f"{result.park_reason}: {result.park_detail}"
    assert (
        len(page.coordinate_clicks) == 1
    ), f"{model} produced no actuated coordinate; steps={[s.note for s in result.steps]}"
    x, y = page.coordinate_clicks[0]
    assert 0.0 <= x <= 800.0 and 0.0 <= y <= 600.0, (x, y)


def _live_provider(model: str):
    """A minimal ModelProvider over the REAL local Ollama, using the platform's content blocks.

    Deliberately not the bundled ``ollama-models`` app provider: importing an app bundle from a core
    test would cross the SDK boundary the core is lint-fenced against. This speaks the same wire
    Ollama does and performs the same content-block split the bundle performs, so what it proves is
    that a real multimodal call through this message shape returns a groundable answer.
    """
    from personalclaw.llm.base import EVENT_TEXT_CHUNK

    class _Event:
        def __init__(self, text: str) -> None:
            self.kind = EVENT_TEXT_CHUNK
            self.text = text

    class _LiveProvider:
        async def complete(self, messages, **_kwargs):
            blocks = messages[0]["content"]
            text = "\n".join(b["text"] for b in blocks if b.get("type") == "text")
            images = [
                b["image_url"]["url"].split(",", 1)[1]
                for b in blocks
                if b.get("type") == "image_url" and "," in b["image_url"]["url"]
            ]
            body = json.dumps(
                {
                    "model": model,
                    "stream": False,
                    "options": {"temperature": 0},
                    "messages": [{"role": "user", "content": text, "images": images}],
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                f"{_OLLAMA_HOST}/api/chat",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            import asyncio

            def _post() -> str:
                with urllib.request.urlopen(request, timeout=300) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                return str((payload.get("message") or {}).get("content") or "")

            yield _Event(await asyncio.get_running_loop().run_in_executor(None, _post))

    return _LiveProvider()


def _target_png(tmp_path: Path) -> str:
    """An 800x600 white PNG with one solid red 120x120 square, written with zlib+struct only.

    Hand-encoded rather than pulled from Pillow: the repo must not gain an image dependency for a
    test fixture, and a real grounding model needs a real image with an unambiguous target.
    """
    import struct
    import zlib

    width, height = 800, 600
    box = (300, 200, 420, 320)  # x0, y0, x1, y1
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # PNG filter type 0 per scanline
        for x in range(width):
            if box[0] <= x < box[2] and box[1] <= y < box[3]:
                rows += b"\xd0\x10\x10"
            else:
                rows += b"\xff\xff\xff"

    def _chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + _chunk(b"IEND", b"")
    )
    path = tmp_path / "target.png"
    path.write_bytes(png)
    return str(path)
