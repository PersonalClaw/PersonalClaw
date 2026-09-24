"""``best-of-n`` action provider — the engine-native half of HARNESS-CRAFT §2.3 (HC-5).

A thin spec wrapper over :func:`personalclaw.sampling.best_of_n` — the §2.1 core the
bundled ``best-of-n`` skill and the ``best_of_n`` MCP tool already call — so the
workflow template, the skill and the tool share ONE implementation and cannot grow two
behaviors for one name. Everything that makes the primitive trustworthy lives in the
core, not here: the genuinely-parallel fan-out, the temperature ladder, the
ModelCallGuard metering on every call, the deterministic winner selection, and the
bounded ``sampling_outcomes.jsonl`` record.

**DEVIATION from §2.3's node sketch, recorded here because this is the seam it names.**
The plan sketches "fan-out node (N samples) → judge node → select node" and, in the
same sentence, requires that "the template CALLS the §2.1 core rather than
reimplementing". The shipped core does not decompose into those halves: its pieces
(``_sample_one`` / ``_judge_candidates`` / ``_select_winner``) are private, and the
concurrency proof, the fail-open tiers, the tie-break contract and the outcome record
all span the WHOLE call. Splitting them across engine nodes would re-own each of those
contracts in template config — exactly the skill/template drift the plan's own risk
table forbids ("templates are thin spec wrappers"). So the template's one action node
calls ``best_of_n`` whole: the engine sees fan-out → judge → select as one metered
action, and the parallelism is the core's own ``asyncio.gather``.

``action_config`` shape (mirrors the MCP tool's arguments exactly)::

    {
        "prompt": "the ask, identical for every candidate",   # required
        "n": 3,                                                # optional; core clamps to 1..5
        "criteria": "what 'best' means for this call"          # optional
    }

Output is the core's envelope verbatim, so a template binding reads the same contract
the skill's presenting model does: ``{winner, winner_idx, candidates, judgments,
judged, n, note}``. An all-N-failed slate (``winner=None``) fails the node — the
engine's honest rendering of "the sampling produced nothing" — with the full envelope
still in the output for the run record.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)

logger = logging.getLogger(__name__)


class BestOfNActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "best-of-n"

    @property
    def display_name(self) -> str:
        return "Best-of-N Sampling"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        import json

        from personalclaw.sampling import best_of_n

        prompt = str(action_config.get("prompt", "") or "").strip()
        if not prompt:
            return ActionResult(success=False, error="best-of-n requires a non-empty 'prompt'")
        # Same coercions as the MCP tool (`mcp_subagents._best_of_n`), so the two entry
        # points hand the core identical arguments for identical inputs — the parity the
        # HC-5 shared-core test asserts.
        try:
            n = int(action_config.get("n") or 3)
        except (TypeError, ValueError):
            return ActionResult(
                success=False, error=f"best-of-n: 'n' must be a number, got {action_config['n']!r}"
            )
        criteria = str(action_config.get("criteria", "") or "")

        try:
            result = await best_of_n(prompt, n, criteria)
        except Exception as exc:  # noqa: BLE001 — an error result, never a raise
            return ActionResult(
                success=False, error=f"best-of-n sampling failed: {type(exc).__name__}: {exc}"
            )

        payload = json.dumps(result, ensure_ascii=False)
        if result.get("winner") is None:
            # The core's fail-open floor: an explicit no-candidate envelope. For a
            # workflow that is a FAILED node — a downstream binding must not consume
            # `winner: null` as if something was selected — with the slate kept in the
            # output so the run record shows what happened.
            return ActionResult(
                success=False,
                error=str(result.get("note") or "no candidate: every sampling call failed"),
                stdout=payload,
            )
        return ActionResult(success=True, stdout=payload)


def create_provider(config: dict[str, Any] | None = None) -> "BestOfNActionProvider":
    return BestOfNActionProvider()
