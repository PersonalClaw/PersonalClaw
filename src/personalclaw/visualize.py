"""``visualize(data, hint)`` — the one agency-free generative-UI primitive
(AMBIENT-SURFACES §5.3).

The two-step pattern: a reasoning agent produces *data*; this SEPARATE, no-tools
step renders it into a genui widget spec. It is "agency-free" by construction — it
resolves through :func:`one_shot_completion` on the **reasoning** use-case axis,
which builds a plain model provider (never the NativeAgentRuntime that ``chat``/
``code_tools`` return), so there are no tools to call: it can only turn data into a
widget spec, never act. Output is constrained to the registry DSL (``genui.py``'s
catalog) and validated FE-side per §5.2 (unknown/invalid lines drop, never crash).

One shared mechanism behind every producer — the ``visualize`` MCP tool, the
WORKFLOWS-V2 ``visualize`` node, cockpit summaries, tiles, digests, "chart this"
chat asks. Keeping the single ``one_shot_completion`` call here (not duplicated in
each caller) is why only THIS file appears in the degraded-contract lint map.

That single funnel is also why ``ambient.genui_enabled`` is honoured HERE. The switch
shipped with a Settings control and ZERO readers (issue #3490) while promising to gate
"agent-authored widgets"; the promise is keepable in one place precisely because every
producer of a widget spec goes through :func:`visualize`. Off raises
:class:`GenUiDisabled` before a prompt is built, so the switch also costs no tokens —
both call sites already map an exception to their own surface's refusal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from personalclaw.genui import library_prompt


class GenUiDisabled(RuntimeError):
    """``ambient.genui_enabled`` is off, so no widget spec may be authored.

    A distinct type rather than a bare ``RuntimeError`` because the two callers must tell
    "the user turned this off" apart from "no reasoning model is bound" — the messages point
    at different Settings pages, and a refusal naming the wrong one is worse than none.
    """


#: The message every surface shows for a disabled generative UI. Defined once so the MCP
#: tool and the workflow node cannot describe one switch two ways.
GENUI_DISABLED_MESSAGE = (
    "generative UI is off — turn on Settings → Ambient surfaces → Generative UI to render "
    "agent-authored widgets, or present the data as text"
)


def genui_enabled() -> bool:
    """``ambient.genui_enabled``. Fails OPEN (unreadable config ⇒ True) to match the
    field's own default: a config blip must not silently stop rendering widgets a user
    has enabled, and authoring a widget spends only the reasoning call this module
    already owns."""
    try:
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().ambient.genui_enabled)
    except Exception:
        return True


@dataclass(frozen=True)
class Visualization:
    """The primitive's result. ``dsl`` is the raw genui DSL body; ``widget`` wraps it
    in the ``<widget kind="genui">`` block a reply/tile embeds directly."""

    dsl: str
    widget: str


def _coerce_data(data: Any) -> str:
    """Render the caller's data as compact text for the prompt. A dict/list becomes
    JSON (the model reads it structurally); a string passes through."""
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, ensure_ascii=False, default=str)[:8000]
    except (TypeError, ValueError):
        return str(data)[:8000]


def _build_prompt(data: Any, hint: str) -> str:
    """The generation prompt: the CURRENT registry vocabulary + the data + the hint,
    asking for ONLY the DSL. The vocabulary is derived (``library_prompt``) so it is
    never a hand-maintained, drifting copy."""
    ask = (hint or "").strip() or "Present this data clearly."
    return (
        f"{library_prompt()}\n\n"
        "Render the DATA below as a compact generative-UI widget using ONLY the "
        "components above. Output ONLY the DSL — one `id = Component(...)` line per "
        "component, no prose, no code fences, no <widget> tags.\n\n"
        f"GOAL: {ask}\n\nDATA:\n{_coerce_data(data)}"
    )


def _clean_dsl(text: str) -> str:
    """Strip anything the model wrapped the DSL in — ``` fences and stray
    ``<widget>`` tags — leaving the bare DSL body the renderer parses."""
    body = (text or "").strip()
    if body.startswith("```"):
        # Drop the opening fence line (``` or ```lang) and a trailing fence.
        lines = body.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    # Remove a wrapping <widget …>…</widget> if the model added one anyway.
    if body.startswith("<widget"):
        start = body.find(">")
        end = body.rfind("</widget>")
        if start != -1:
            body = body[start + 1 : (end if end != -1 else len(body))].strip()
    return body


def _wrap_widget(dsl: str, title: str) -> str:
    safe_title = (title or "Visualization").replace('"', "'")
    return f'<widget kind="genui" title="{safe_title}">\n{dsl}\n</widget>'


async def visualize(
    data: Any,
    hint: str = "",
    *,
    title: str = "Visualization",
    completion: Any = None,
) -> Visualization:
    """Turn ``(data, hint)`` into a genui widget spec, agency-free.

    ``completion`` is injected so tests (and the WF2 executor) can drive this
    without a live provider; production leaves it ``None`` and this resolves
    :func:`one_shot_completion` on the reasoning axis. Raises on a provider/model
    failure (the caller maps it to its own surface's degraded floor: no
    visualization produced, the raw data still available).

    Raises :class:`GenUiDisabled` when ``ambient.genui_enabled`` is off — checked before
    the prompt is built, and before ``completion`` is consulted, so a disabled switch is
    honoured identically on the injected and the production path."""
    if not genui_enabled():
        raise GenUiDisabled(GENUI_DISABLED_MESSAGE)
    prompt = _build_prompt(data, hint)
    if completion is not None:
        text = await completion(prompt, use_case="reasoning")
    else:
        # The reasoning axis (never chat/code_tools) builds a plain model provider —
        # no tools, no agent runtime — which is what makes this primitive agency-free.
        # This is the surface the degraded-contract lint maps (assistant_reasoning).
        from personalclaw.llm_helpers import one_shot_completion

        text = await one_shot_completion(prompt, use_case="reasoning")
    dsl = _clean_dsl(str(text or ""))
    return Visualization(dsl=dsl, widget=_wrap_widget(dsl, title))
