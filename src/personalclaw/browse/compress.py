"""Context compression layer — the ~20x token reduction that makes autonomous browse viable
(plan amendment 2026-07-26(a), "Context compression layer + task A1").

The measured problem: raw DOM is ~100K+ tokens; a browse node cannot afford to put that in a
model's context every step. This module sits between the (structural) extraction and the
agent's prompt and produces a ``PageOutline`` — a compact, ref-addressable representation that
fits under a small token budget:

  * the ≤4000-char text body, further trimmed to the outline's own budget;
  * the interactive elements (links + form fields) as ref lines — the surface the agent acts
    on, which is what a text-only representation is *for*;
  * a **screenshot referenced by PATH only** — ``[SCREENSHOT: <path>]``. A screenshot is
    captured per step for verification, but it enters context as a path placeholder, NEVER as
    a ``data:image`` base64 blob. Inlining even one base64 screenshot would blow the entire
    token budget this layer exists to defend (a single 1MP PNG is ~1MB → ~350K tokens of
    base64) and defeats the point. ``assert_no_base64`` is the belt-and-suspenders guard the
    regression test drives.

Token counting reuses ``learning.surfacing.count_tokens`` (tiktoken when installed, char/4
otherwise) so the "<1K tokens" contract is measured with the SAME estimator the rest of the
codebase budgets against — not a second, divergent counter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from personalclaw.browse.extraction import (
    ElementRef,
    PageExtraction,
    render_links_dsl,
)
from personalclaw.learning.surfacing import count_tokens

#: Default outline budget. The plan's target is <1K tokens for a 100K-token DOM; we compress
#: to comfortably under that, leaving headroom for the loop's system prompt + goal + notes.
DEFAULT_MAX_TOKENS = 800

#: A base64 image payload (``data:image/png;base64,iVBORw0K…``) — the one thing that must
#: never appear in a rendered outline. Detected structurally, not by exact string, so a
#: differently-cased or whitespaced variant is still caught.
_BASE64_IMG_RE = re.compile(r"data:image/[a-z.+-]+;base64,", re.IGNORECASE)


@dataclass(frozen=True)
class PageOutline:
    """The compact context representation of a page (amendment 2026-07-26(a) contract).

    ``elements`` are the interactive refs (links + fields) an agent can address; ``text`` is
    the trimmed body; ``screenshot_path`` is a filesystem path under the run workspace —
    NEVER base64. ``render()`` is what enters the prompt.
    """

    url: str
    text: str
    elements: tuple[ElementRef, ...]
    screenshot_path: str = ""

    def render(self) -> str:
        """The prompt-facing string: text, interactive elements, and a screenshot PATH line."""
        parts: list[str] = []
        if self.url:
            parts.append(f"# {self.url}")
        if self.text:
            parts.append(self.text)
        links = [e for e in self.elements if e.role == "link"]
        fields = [e for e in self.elements if e.role != "link"]
        if links:
            parts.append(render_links_dsl(links))
        if fields:
            parts.append(_render_field_lines(fields))
        if self.screenshot_path:
            # Path placeholder only — a multimodal step may explicitly load the file, but it
            # never rides inline as base64. (plan §7 verification / amendment (a))
            parts.append(f"[SCREENSHOT: {self.screenshot_path}]")
        return "\n\n".join(parts)


def _render_field_lines(fields: list[ElementRef]) -> str:
    lines = ["## Elements"]
    for e in fields:
        note = f" {e.note}" if e.note else ""
        state = f" ({e.state})" if e.state else ""
        lines.append(f"[{e.ref}] {e.role}: {e.label}{state}{note}")
    return "\n".join(lines)


def _trim_text_to_budget(text: str, elements_tokens: int, max_tokens: int) -> str:
    """Shrink the text body so text + elements fit under ``max_tokens``.

    Elements are load-bearing (an agent cannot act without refs), so the text body is the
    sacrificial part: it is truncated on a word boundary until the whole outline fits. This
    mirrors surfacing's "exactly one sacrificial slot" discipline."""
    budget = max_tokens - elements_tokens
    if budget <= 0:
        return ""  # elements alone already saturate the budget; drop prose entirely
    if count_tokens(text) <= budget:
        return text
    # char/4 is the estimator's basis; start there and shrink until under budget.
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count_tokens(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    trimmed = text[:lo].rstrip()
    # Prefer a clean word boundary over a mid-word cut when one is close by.
    sp = trimmed.rfind(" ")
    if sp > 0 and lo - sp < 24:
        trimmed = trimmed[:sp].rstrip()
    return trimmed + " …" if trimmed else ""


def compress_page(
    extraction: PageExtraction,
    *,
    screenshot_path: str = "",
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> PageOutline:
    """Compress a full ``PageExtraction`` into a token-budgeted ``PageOutline``.

    Priority under the budget (a ranking, not just a counter — the surfacing discipline):
    **form fields** are kept whole (they are the TYPE/SUBMIT surface an agent cannot act
    without), **links** are trimmed to the top-N that still fit (§1.2 already calls them
    "top-N navigable"; a 2000-link nav forest must not blow the budget), and the **text body**
    is trimmed last into whatever remains. The screenshot is carried as a path, never inlined.
    """
    fields: list[ElementRef] = []
    for form in extraction.forms:
        fields.extend(form.fields)

    tail = f"[SCREENSHOT: {screenshot_path}]" if screenshot_path else ""
    header = f"# {extraction.url}" if extraction.url else ""
    field_lines = _render_field_lines(fields) if fields else ""

    # Form fields are never dropped (the TYPE/SUBMIT surface an agent cannot act without); if
    # they alone exceed the budget the outline saturates there (an honest floor, not a lie).
    # Add links greedily until the next one would push the element scaffold over budget.
    kept_links: list[ElementRef] = []
    for link in extraction.links:
        trial = render_links_dsl(kept_links + [link])
        cost = count_tokens("\n\n".join(p for p in (header, trial, field_lines, tail) if p))
        if cost > max_tokens:
            break
        kept_links.append(link)

    scaffold_tokens = count_tokens(
        "\n\n".join(p for p in (header, render_links_dsl(kept_links), field_lines, tail) if p)
    )
    text = _trim_text_to_budget(extraction.text, scaffold_tokens, max_tokens)
    return PageOutline(
        url=extraction.url,
        text=text,
        elements=tuple(kept_links + fields),
        screenshot_path=screenshot_path,
    )


def assert_no_base64(rendered: str) -> None:
    """Raise if a rendered outline/prompt carries an inline base64 image payload.

    The load-bearing regression guard: a screenshot must be a PATH, never a ``data:image``
    blob. Called by the loop before a rendered page enters context (and by the BA-1 regression
    test) so the "no base64 in any rendered prompt" invariant fails loudly, not silently."""
    m = _BASE64_IMG_RE.search(rendered)
    if m:
        raise ValueError(
            "rendered browse context contains an inline base64 image payload "
            f"({m.group(0)!r}) — screenshots MUST be referenced by path, never inlined"
        )
