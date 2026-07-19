"""The unified Loop engine — PersonalClaw's one autonomous primitive.

See :mod:`personalclaw.loop.loop` for the entity + lifecycle and
:mod:`personalclaw.loop.kinds` for the per-kind strategy registry. The store /
manager / watchdog (added in the engine-unification slice) are kind-agnostic and
dispatch behavior through the registry.
"""

from __future__ import annotations

from personalclaw.loop.loop import (
    ACTION_SOURCE_STATES,
    ACTIVE_STATUSES,
    KINDS,
    PRELAUNCH_STATUSES,
    TERMINAL_STATUSES,
    Loop,
    LoopKind,
    LoopStatus,
)

__all__ = [
    "Loop",
    "LoopKind",
    "LoopStatus",
    "KINDS",
    "TERMINAL_STATUSES",
    "ACTIVE_STATUSES",
    "PRELAUNCH_STATUSES",
    "ACTION_SOURCE_STATES",
]
