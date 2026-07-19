"""Goal-kind vocabulary — the valid goal types + granularity dial settings. Pure data,
no deps; lives in the unified ``loop`` package so the goal kind + its classifier don't
reach back into the legacy ``loops`` package (cutover Slice 2e — self-containing
``loop/`` before the legacy engines are deleted)."""

from __future__ import annotations

# verifiable: a deterministic check decides done; open_ended: a judge + the granularity
# dial; monitor: never self-completes (only a user Stop / budget ends it).
GOAL_TYPES: frozenset[str] = frozenset({"verifiable", "open_ended", "monitor"})

# The returns-exhaustion dial — how aggressively an open_ended loop self-stops on
# diminishing marginal value (see loop.granularity for the threshold/window mapping).
GRANULARITIES: frozenset[str] = frozenset({"quick", "balanced", "exhaustive", "forever"})
