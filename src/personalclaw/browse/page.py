"""The real CDP page driver behind :class:`~personalclaw.browse.loop.PageDriver` (BA-3).

Everything the browse loop does to a page that is NOT navigation, spoken over the same
:class:`~personalclaw.browse.cdp.CdpTransport` BA-2 already owns. Navigation is absent by
design and stays with :class:`~personalclaw.browse.cdp.GatedCdpSession`, which pre-flights
every URL through the egress guard — a driver able to navigate would be a second, ungated
route to the network.

**Elements are addressed by IDENTITY, not by position.** BA-1's ``ElementRef`` is
``sha1(role + accessible name + form)`` precisely so a ref survives an unrelated DOM
mutation; a CSS path or an nth-child index would not. So the driver re-finds the element in
the live DOM from the same three identity fields the ref was minted from, preferring a link's
``href`` when it has one (the strongest identity a page offers). Colliding identities resolve
to the FIRST match — documented rather than papered over, because the alternative (an
ordinal the extraction does not expose) would be a guess dressed as precision.

**One action is addressed by COORDINATE, and only when asked for by name.** ``click_at`` (BA-10)
posts a located ``Input.dispatchMouseEvent`` for the page shape that has no identity to address at
all — a ``<canvas>`` or image-map, where the extraction yields zero refs. It is reached only through
the loop's explicitly-enabled vision path, never as a fallback from a failed ref lookup, mirroring
the desktop driver's rule that ``auto`` never resolves onto a coordinate method.

**A screenshot is written to a PATH.** ``Page.captureScreenshot`` answers base64; this module
decodes it to a file under a caller-supplied directory and hands back the path, because the
one thing BA-1's compression contract forbids is an image payload riding into the prompt.
With no directory configured it returns ``""`` — no capture is strictly better than a
megabyte of context.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from personalclaw.browse.extraction import (
    ROLE_BUTTON,
    ROLE_CHECKBOX,
    ROLE_LINK,
    ROLE_SELECT,
    ElementRef,
)

logger = logging.getLogger(__name__)

EVALUATE = "Runtime.evaluate"
CAPTURE_SCREENSHOT = "Page.captureScreenshot"
#: BA-10's located input event. A real mouse event delivered to the PAGE at a coordinate — which is
#: the whole point: a canvas listens for ``mousedown``/``mouseup``, not for the synthetic
#: ``HTMLElement.click()`` the ref path dispatches, so the ref path cannot drive one even if the
#: canvas somehow had a ref. Named here rather than inline so the one verb that actuates a
#: coordinate is grep-able from the transport's method list.
DISPATCH_MOUSE_EVENT = "Input.dispatchMouseEvent"

#: What the locator answers when the identity matched nothing live. Surfaced as a raised
#: :class:`PageActionError` so the loop records a warning the model can act on rather than
#: reporting a click that never happened.
NOT_FOUND = "not-found"

_SELECTOR_BY_ROLE = {
    ROLE_LINK: "a[href]",
    ROLE_BUTTON: "button, input[type=submit], input[type=button], [role=button]",
    ROLE_SELECT: "select",
    ROLE_CHECKBOX: "input[type=checkbox], input[type=radio]",
}
_FIELD_SELECTOR = "input, textarea, select"

#: Shared JS prologue: normalise whitespace/case, derive the accessible name the same way
#: ``extraction._handle_input`` does, and read the owning form's name/id.
_LOCATOR_PRELUDE = """
const norm = (s) => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();
const nameOf = (el) => norm(
  el.getAttribute("aria-label") ||
  ((el.labels && el.labels[0]) ? el.labels[0].textContent : "") ||
  el.getAttribute("placeholder") ||
  el.getAttribute("name") ||
  el.getAttribute("id") ||
  ((el.tagName === "INPUT" || el.tagName === "BUTTON") ? el.getAttribute("value") : "") ||
  el.textContent
);
const formOf = (el) => norm(
  el.form ? (el.form.getAttribute("name") || el.form.getAttribute("id") || "") : ""
);
const all = Array.from(document.querySelectorAll(SELECTOR));
let hit = null;
if (ROLE === "link" && TARGET) {
  hit = all.find((el) => el.getAttribute("href") === TARGET);
}
if (!hit) {
  hit = all.find((el) => nameOf(el) === norm(LABEL) && (!FORM || formOf(el) === norm(FORM)));
}
if (!hit) { hit = all.find((el) => nameOf(el) === norm(LABEL)); }
if (!hit) { return NOTFOUND; }
"""


#: The placeholder names :func:`_js` substitutes, matched in one pass.
_PLACEHOLDER_RE = re.compile(r"\b(?:SELECTOR|ROLE|LABEL|FORM|TARGET|NOTFOUND|VALUE)\b")


class PageActionError(RuntimeError):
    """A page action could not be performed (element gone, evaluate rejected)."""


def _js(body: str, *, ref: ElementRef, extra: dict[str, str] | None = None) -> str:
    """Compose one self-contained expression: prelude + ``body``, with identity substituted.

    Values go in through ``json.dumps`` — a page's own label text reaches this function and a
    naive f-string would let a link captioned ``");alert(1)//`` write the expression.
    """
    selector = _SELECTOR_BY_ROLE.get(ref.role, _FIELD_SELECTOR)
    subs = {
        "SELECTOR": json.dumps(selector),
        "ROLE": json.dumps(ref.role),
        "LABEL": json.dumps(ref.label),
        "FORM": json.dumps(ref.form),
        "TARGET": json.dumps(ref.target),
        "NOTFOUND": json.dumps(NOT_FOUND),
    }
    subs.update(extra or {})
    # ONE pass, not a loop of `.replace()`. A page controls `label`/`target`, so a label
    # containing the literal text `VALUE` would be rewritten by a later replacement in a
    # sequential loop — the substituted content becoming a substitution target is the classic
    # way an escaping scheme escapes itself.
    source = _PLACEHOLDER_RE.sub(lambda m: subs[m.group(0)], _LOCATOR_PRELUDE + body)
    return "(() => {" + source + "})()"


class CdpPageDriver:
    """A :class:`~personalclaw.browse.loop.PageDriver` over one CDP page target.

    Holds the transport BA-2's session holds — the same connection, a different verb set —
    so there is exactly one socket per page and exactly one gate in front of navigation.
    """

    def __init__(self, transport: Any, *, screenshot_dir: Path | None = None) -> None:
        self._transport = transport
        self._screenshot_dir = screenshot_dir

    # ── reading ──────────────────────────────────────────────────────────────

    async def _eval(self, expression: str) -> Any:
        reply = await self._transport.send(
            EVALUATE, {"expression": expression, "returnByValue": True, "awaitPromise": True}
        )
        details = reply.get("exceptionDetails") if isinstance(reply, dict) else None
        if details:
            text = str(details.get("text") or details)
            raise PageActionError(f"the page rejected the expression: {text}")
        result = reply.get("result") if isinstance(reply, dict) else None
        if isinstance(result, dict):
            return result.get("value")
        return None

    async def html(self) -> str:
        value = await self._eval("document.documentElement.outerHTML || ''")
        return str(value or "")

    async def current_url(self) -> str:
        value = await self._eval("document.location ? document.location.href : ''")
        return str(value or "")

    async def viewport(self) -> tuple[float, float]:
        """The CSS viewport as ``(width, height)``; ``(0.0, 0.0)`` when it cannot be read.

        CSS pixels, not device pixels, because that is the unit ``Input.dispatchMouseEvent`` takes.
        A screenshot is captured at the device pixel ratio, so scaling a grounded fraction by the
        IMAGE's size would be out by that ratio on every retina display — the caller multiplies by
        this instead, and the ratio never enters the arithmetic (see :mod:`browse.vision`).

        A zero answer is a refusal, not a default: the caller must not actuate at ``(0, 0)``, which
        is the top-left corner of every page and would click whatever sits there.
        """
        raw = await self._eval("JSON.stringify([window.innerWidth, window.innerHeight])")
        try:
            width, height = json.loads(str(raw))
            return float(width), float(height)
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.debug("browse: viewport unreadable (%r)", raw)
            return 0.0, 0.0

    # ── acting ───────────────────────────────────────────────────────────────

    async def _act(self, expression: str, what: str) -> None:
        value = await self._eval(expression)
        if value == NOT_FOUND:
            raise PageActionError(f"{what}: no matching element is on the page any more")

    async def click(self, ref: ElementRef) -> None:
        await self._act(_js("hit.click(); return 'ok';", ref=ref), f"CLICK {ref.ref}")

    async def fill(self, ref: ElementRef, value: str) -> None:
        body = (
            "hit.focus();"
            "if (hit.type === 'checkbox' || hit.type === 'radio') {"
            "  hit.checked = VALUE !== 'false' && VALUE !== '';"
            "} else { hit.value = VALUE; }"
            "hit.dispatchEvent(new Event('input', {bubbles: true}));"
            "hit.dispatchEvent(new Event('change', {bubbles: true}));"
            "return 'ok';"
        )
        await self._act(_js(body, ref=ref, extra={"VALUE": json.dumps(value)}), f"TYPE {ref.ref}")

    async def submit(self) -> None:
        """Submit the form the agent has been filling.

        The FOCUSED element's form first, then the document's only form. Order matters: after
        a TYPE the focused field is the one true answer to "the current form", and picking
        ``document.forms[0]`` on a page with a search box in the header would submit the
        search instead of the thing the agent filled in.
        """
        expression = (
            "(() => {"
            "const active = document.activeElement;"
            "let form = active && active.form ? active.form : null;"
            "if (!form) { form = document.forms.length ? document.forms[0] : null; }"
            "if (!form) { return 'no-form'; }"
            "const btn = form.querySelector("
            "  'button[type=submit], input[type=submit], button:not([type])');"
            "if (btn) { btn.click(); return 'ok'; }"
            "if (form.requestSubmit) { form.requestSubmit(); return 'ok'; }"
            "form.submit(); return 'ok';"
            "})()"
        )
        value = await self._eval(expression)
        if value == "no-form":
            raise PageActionError("SUBMIT: there is no form on this page")

    async def click_at(self, x: float, y: float) -> None:
        """BA-10: click a viewport COORDINATE with a located CDP input event.

        Three events, in the order a real pointer produces them: a ``mouseMoved`` so a canvas that
        tracks hover has a cursor where the click lands, then the press and the release. A bare
        ``mousePressed``/``mouseReleased`` pair works on simple handlers and fails on every UI that
        reads ``mousemove`` first — which is most canvas UIs, and the failure looks like bad
        grounding rather than a missing event.

        "Located" in the same sense the desktop driver means it (``macos_ffi.click_located``): the
        event is delivered to THIS page target over the session's own socket, so it cannot land in
        another tab or move anything the operator owns. There is no browse analogue of
        ``click_global`` and there must never be one — a coordinate that could escape the page is a
        different capability from driving a page.

        Refuses a non-finite or negative coordinate rather than posting it: CDP accepts an
        off-viewport point silently, so a scaling bug would become an event nobody can find.
        """
        for name, value in (("x", x), ("y", y)):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise PageActionError(f"a located click needs a numeric {name}")
            if value != value or value in (float("inf"), float("-inf")) or value < 0:
                raise PageActionError(f"a located click needs a finite, non-negative {name}")
        base = {"x": float(x), "y": float(y)}
        for phase, button, buttons in (
            ("mouseMoved", "none", 0),
            ("mousePressed", "left", 1),
            ("mouseReleased", "left", 0),
        ):
            params = {**base, "type": phase, "button": button, "buttons": buttons}
            if phase != "mouseMoved":
                params["clickCount"] = 1
            await self._transport.send(DISPATCH_MOUSE_EVENT, params)

    async def scroll(self, direction: str) -> None:
        delta = "-window.innerHeight" if str(direction).lower() == "up" else "window.innerHeight"
        await self._eval(f"window.scrollBy(0, {delta}); 'ok'")

    async def go_back(self) -> None:
        await self._eval("history.back(); 'ok'")

    # ── capture ──────────────────────────────────────────────────────────────

    async def screenshot(self) -> str:
        """Capture to a file and return its PATH. "" when there is nowhere to write.

        Best-effort by contract: a failed capture must not end a browse run, because the
        screenshot is an aid to verification and the extracted text is the actual perception.
        """
        if self._screenshot_dir is None:
            return ""
        try:
            reply = await self._transport.send(CAPTURE_SCREENSHOT, {"format": "png"})
            data = str((reply or {}).get("data") or "")
            if not data:
                return ""
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)
            path = self._screenshot_dir / f"browse-{int(time.time())}-{uuid.uuid4().hex[:8]}.png"
            path.write_bytes(base64.b64decode(data))
            return str(path)
        except Exception:
            logger.debug("browse: screenshot capture failed", exc_info=True)
            return ""
