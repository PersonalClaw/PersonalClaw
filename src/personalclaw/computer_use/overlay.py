"""The cursor-motion overlay's data source — observation of acting calls (`DCU-7`, §3.7).

**This module GRANTS NOTHING.** DESKTOP-COMPUTER-USE §3 floor 7 is the whole spec: *"The
human-facing views grant nothing: … an optional cursor-motion overlay draws a fake cursor so
a watching human sees where a click will land — it is not the real pointer and is invisible
to screen capture."* The fake cursor here is a RECORD, not a pointer: the dispatch tells this
module where an approved acting call is about to land, the record sits in a bounded
in-process buffer, and the dashboard's live view draws it. Nothing in this module can move,
click, type, walk a window, or reach a driver — ``tests/test_computer_use_live_view.py``
asserts that by AST, the same way the shim's thinness is asserted, and the capability census
(`test_the_tool_surface_is_unchanged_with_the_views_on`) pins that turning the views on
changes the tool surface not at all.

**Why the fake cursor is drawn in the dashboard rather than on the desktop.** The plan's
"invisible to screen capture" clause is a constraint, not a technique: the overlay must never
contaminate what the agent (or any capture of the driven desktop) can see. Drawing it in the
operator's browser satisfies that by construction — the marker exposes no accessibility
element for ``computer_snapshot`` to index and paints no pixel on the driven display — where
a native always-on-top window would have to *earn* the invisibility (`NSWindowSharingNone`)
and would be this package's second OS-drawing surface for zero capability payoff. The
dashboard is also where the watching human already is. Recorded as a deviation in the plan's
execution log, because the plan's PiP phrasing predates `DCU-3`'s decision to read
accessibility trees rather than capture pixels.

**Why observing cannot refuse, and cannot raise.** The observation runs inside
:func:`personalclaw.computer_use.service.computer_dispatch`, between the approved SEL row and
the acting driver call — before the action lands, which is what makes "sees where a click
WILL land" the true tense. A view seam that can raise there is a decision-maker by accident
(the same reasoning as :func:`personalclaw.computer_use.gate.require_computer_use`): a malformed
element would turn "show the human" into "refuse the drive". So :func:`observe_action`
swallows everything and returns ``None`` on any input.

**Why the buffer may hold window text the SEL may not.** :mod:`gate`'s shape-summary rule
exists because the SEL is *persisted* — a window title written there sits on disk forever.
This trail is process-local, bounded at :data:`MAX_TRAIL`, never persisted, and rendered only
to the operator whose own desktop it describes. The one rule it inherits is the model's:
every string is passed through :func:`personalclaw.security.redact_credentials` on the way
in, so the view mirrors at most what the dispatch would hand the model, never more.
"""

from __future__ import annotations

import itertools
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: How many trail points the overlay remembers. A ceiling on memory, like
#: ``service.MAX_LIVE_SNAPSHOTS``: the overlay shows a watching human what is happening NOW,
#: so points past a few screens of motion are history the SEL already keeps better.
MAX_TRAIL = 60

#: How the fake cursor is spelled per click path. ``ax_press`` is the default press that
#: moves no pointer at all — the fake cursor is the ONLY cursor that "moves" for it, which is
#: exactly the case the overlay exists for. The other two are the plan's own method names.
_AX_PRESS = "ax_press"

#: Longest element label a trail point carries. The label orients a human ("Save · AXButton"),
#: it is not a transcript; anything longer is a window's prose leaking into a view model.
_MAX_LABEL = 80


@dataclass(frozen=True)
class TrailPoint:
    """One observed acting call: where it will land, and by which pointer story.

    ``x``/``y`` are screen coordinates (the element frame's centre, or the explicit
    coordinates of a named pointer method) and are ``None`` when the driver never reported a
    frame — a point the view draws nothing for, honestly, rather than a (0, 0) that would
    paint the fake cursor in the display corner.
    """

    seq: int
    ts: float
    tool: str
    app: str
    method: str
    x: float | None
    y: float | None
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "tool": self.tool,
            "app": self.app,
            "method": self.method,
            "x": self.x,
            "y": self.y,
            "label": self.label,
        }


#: The trail, oldest first. ``deque(maxlen=…)`` so the bound is structural — there is no
#: prune step to forget. Process-local like ``service._SNAPSHOTS``, and for the same reason:
#: a point is a claim about a desktop this process just acted on.
_TRAIL: deque[TrailPoint] = deque(maxlen=MAX_TRAIL)

_SEQ = itertools.count(1)


def _scrub(text: str) -> str:
    from personalclaw.security import redact_credentials

    cleaned, _warnings = redact_credentials(str(text))
    return cleaned


def _point_of(element: dict | None, params: dict) -> tuple[float | None, float | None]:
    """Where the call lands: the fresh element's frame centre, else the named coordinates."""
    if isinstance(element, dict):
        frame = element.get("frame")
        if isinstance(frame, dict):
            try:
                x = float(frame.get("x", 0.0))
                y = float(frame.get("y", 0.0))
                width = float(frame.get("width", 0.0))
                height = float(frame.get("height", 0.0))
            except (TypeError, ValueError):
                return None, None
            if width > 0 or height > 0 or x or y:
                return x + width / 2, y + height / 2
    x_arg, y_arg = params.get("x"), params.get("y")
    if isinstance(x_arg, (int, float)) and isinstance(y_arg, (int, float)):
        return float(x_arg), float(y_arg)
    return None, None


def _label_of(element: dict | None) -> str:
    """A short, scrubbed 'what is being touched' — title over role, both when both exist."""
    if not isinstance(element, dict):
        return ""
    title = str(element.get("title") or element.get("label") or "").strip()
    role = str(element.get("role") or "").strip()
    label = " · ".join(part for part in (title, role) if part)
    return _scrub(label)[:_MAX_LABEL]


def _method_of(tool: str, params: dict) -> str:
    """The pointer story, in the plan's vocabulary. Duplicates NO policy: the dispatch has
    already validated the method by the time an approved call reaches here, so an unknown
    spelling can only mean a non-click tool — which is an accessibility-path action."""
    if tool == "computer_click":
        raw = params.get("click_method")
        method = raw.strip() if isinstance(raw, str) and raw.strip() else "auto"
        if method in ("located", "global"):
            return method
    return _AX_PRESS


def observe_action(
    *, tool: str, app: str, element: dict | None = None, params: dict | None = None
) -> None:
    """Record where one APPROVED acting call is about to land. Never decides, never raises.

    Called by the dispatch after the approved SEL row and before the acting driver call —
    so a driver that wedges or fails still shows the human what was attempted, and a refused
    attempt never paints a fake cursor over a click that will not happen (the refusal is the
    feed's job, from the SEL). Failing open mirrors ``gate.require_computer_use``: an
    observation seam that can raise inside the chain would be a second decision site.
    """
    try:
        args = params if isinstance(params, dict) else {}
        x, y = _point_of(element, args)
        _TRAIL.append(
            TrailPoint(
                seq=next(_SEQ),
                ts=time.time(),
                tool=_scrub(str(tool))[:_MAX_LABEL],
                app=_scrub(str(app))[:_MAX_LABEL],
                method=_method_of(str(tool), args),
                x=x,
                y=y,
                label=_label_of(element),
            )
        )
    except Exception:
        # Fail OPEN, loudly enough to fix: the overlay is observation, and a broken
        # observation must cost the view a point, never the operator a drive.
        logger.warning("computer_use overlay observation dropped (tool=%r)", tool, exc_info=True)


def motion_trail(limit: int = MAX_TRAIL) -> list[dict[str, Any]]:
    """The trail as the view draws it: oldest first, so consecutive points are the motion."""
    points = list(_TRAIL)
    if limit >= 0:
        points = points[-limit:] if limit else []
    return [point.to_dict() for point in points]


def reset_trail() -> None:
    """Drop every trail point. For tests, mirroring ``service.reset_snapshots``."""
    _TRAIL.clear()
