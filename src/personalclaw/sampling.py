"""Best-of-N sampling — the core primitive (HARNESS-CRAFT §2.1, HC-3).

``best_of_n(prompt, n, judge_criteria, use_case)`` fires N **genuinely parallel**,
temperature-varied :func:`~personalclaw.llm_helpers.one_shot_completion` calls, has
:class:`~personalclaw.eval.judge.LLMJudge` score every survivor against
``judge_criteria``, and returns the winner plus the full slate (candidates +
judgments) so a caller can offer "use #2".

Why every design choice is what it is:

* **Parallel, not a loop.** N sequential calls would cost N× latency on top of the
  N× spend, which makes sampling useless for an interactive turn. The fan-out is one
  ``asyncio.gather`` over N tasks; ``tests/test_sampling_best_of_n.py`` asserts the
  in-flight count reaches N (a sequential loop peaks at 1), which no loop can fake.
* **Through the chokepoint.** Each sample rides ``one_shot_completion`` → the
  use-case bridge → :class:`~personalclaw.guardrails.model_call.ModelCallGuard`, so
  the N× cost is metered by the SpendMeter, breaker-guarded, and visible as N lines
  in ``model_calls.jsonl``. There is deliberately no raw-provider shortcut here: a
  primitive whose whole hazard IS cost must not be the one call that escapes the
  meter.
* **Temperature-varied for real.** The ladder is threaded to the provider as a
  genuine sampling parameter (``one_shot_completion(temperature=…)`` →
  ``extra_options["temperature"]`` → the request kwargs both protocol clients
  already forward). Caveat named honestly: an Anthropic model in extended-thinking
  mode *forbids* a custom temperature and drops it (``llm/anthropic.py``), so on such
  a binding the ladder collapses and candidates may come back near-identical — the
  judgment slate then simply shows a zero spread, which is exactly the signal the
  outcome log exists to surface.
* **Partial-tolerant, fail-open.** One failed sample loses that candidate, never the
  call: survivors are judged and a winner is still returned. If *all* N fail, the
  result is an explicit no-candidate envelope (``winner=None`` + ``note``) rather
  than an exception or a fabricated answer. If the judge itself is unavailable the
  slate is returned ``judged=False`` with the lowest-index survivor as winner — the
  no-model floor is "you get one answer, unjudged", never "you get nothing".
* **Deterministic selection.** The winner is ``max(score)`` with ties broken by the
  LOWEST candidate index (equivalently: sort by ``(-score, idx)`` and take the
  first). Same candidates + same judge scores ⇒ same winner, always. Unjudged
  candidates (judge errored on that one) never win over a judged one; with no
  judgments at all the lowest-index survivor wins.

Each call appends one bounded record to ``$PERSONALCLAW_HOME/sampling_outcomes.jsonl``
— ``{ts, n, criteria_digest, winner_idx, score_spread, tokens_total}``, no prompt or
candidate text — the LEARNING-FLYWHEEL / EVALUATION-SUBSTRATE feed for "did sampling
actually help, and is the spread ever meaningful for this use case?". The store is
declared ``derived`` in the durability inventory, which is what keeps it out of
snapshots (claimed, so ``audit_home()`` sees it; disposable, so it is never backed
up).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# The skill's confirmation gate caps N at 5 (HARNESS-CRAFT §2.2); the core enforces
# the same cap so a workflow template or MCP caller can't quietly buy 20× spend.
MAX_N = 5

OUTCOMES_FILENAME = "sampling_outcomes.jsonl"
_MAX_OUTCOME_LINES = 2_000

# A FIXED, index-keyed ladder rather than random jitter: reproducibility is the whole
# point of a deterministic winner. Index 0 stays low-temperature so the "one candidate
# survived" degrade returns the most conservative sample.
_TEMPERATURE_LADDER = (0.2, 0.7, 1.0, 0.45, 0.85)

_JUDGE_DESCRIPTION = (
    "Best-of-N sampling: several candidate responses to the same prompt. "
    "Score this candidate on its own merits against the criteria."
)
_DEFAULT_CRITERIA = (
    "Directly answers the prompt; accurate; concrete and specific; well organized; "
    "no padding or hedging."
)


def _temperatures(n: int) -> list[float]:
    """The first ``n`` rungs of the ladder (n is already clamped to ``MAX_N``)."""
    return list(_TEMPERATURE_LADDER[:n])


async def _sample_one(prompt: str, idx: int, temperature: float, use_case: str) -> dict[str, Any]:
    """One guarded, temperature-pinned completion. Never raises — a failure becomes a
    candidate carrying its ``error`` so the slate stays N-wide and legible."""
    from personalclaw.llm_helpers import one_shot_completion

    try:
        text = await one_shot_completion(prompt, use_case=use_case, temperature=temperature)
    except Exception as exc:  # noqa: BLE001 — a dead candidate must not kill the slate
        logger.warning(
            "best_of_n: candidate %d (temp %.2f) failed: %s: %s",
            idx,
            temperature,
            type(exc).__name__,
            exc,
        )
        return {
            "idx": idx,
            "temperature": temperature,
            "text": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
    if not (text or "").strip():
        return {"idx": idx, "temperature": temperature, "text": "", "error": "empty completion"}
    return {"idx": idx, "temperature": temperature, "text": text, "error": ""}


async def _judge_candidates(
    prompt: str,
    criteria: str,
    survivors: list[dict[str, Any]],
    provider_factory: Any = None,
) -> list[dict[str, Any]]:
    """Score each survivor with LLMJudge. Returns one judgment per SCORED candidate.

    Judge calls are sequential on purpose: ``LLMJudge`` holds ONE provider session and
    concurrent streams over a single session is not a contract any provider offers.
    Two failure floors: the judge failing to start returns ``[]`` (caller degrades to
    unjudged), and a single candidate's judge call failing drops that candidate's
    score without touching the others.
    """
    from personalclaw.eval.judge import LLMJudge

    if provider_factory is None:

        def provider_factory(_session_key: str, **_kw: Any) -> Any:
            from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

            # The reasoning axis — judging a slate IS the reasoning-shaped call, and the
            # bridge wraps it in the same ModelCallGuard the samples ride.
            return resolve_provider_for_use_case("reasoning")

    judge = LLMJudge(provider_factory)
    try:
        await judge.start()
    except Exception:  # noqa: BLE001
        logger.warning(
            "best_of_n: judge provider failed to start — returning the slate UNJUDGED "
            "(no-model floor: one answer, honestly labeled)",
            exc_info=True,
        )
        return []
    judgments: list[dict[str, Any]] = []
    try:
        for cand in survivors:
            try:
                verdict = await judge.judge_turn(
                    _JUDGE_DESCRIPTION, criteria or _DEFAULT_CRITERIA, prompt, cand["text"]
                )
            except Exception:  # noqa: BLE001 — one unscored candidate, not a dead slate
                logger.warning(
                    "best_of_n: judge failed on candidate %d — left unscored", cand["idx"]
                )
                continue
            judgments.append(
                {
                    "idx": cand["idx"],
                    "score": float(verdict.score),
                    "reason": verdict.reason,
                    "reasoning": verdict.reasoning,
                }
            )
    finally:
        try:
            await judge.shutdown()
        except Exception:  # noqa: BLE001
            logger.debug("best_of_n: judge shutdown failed", exc_info=True)
    return judgments


def _select_winner(survivors: list[dict[str, Any]], judgments: list[dict[str, Any]]) -> int:
    """The deterministic pick: highest score, ties broken by the LOWEST index.

    With no judgments at all (judge unavailable) the lowest-index survivor wins — the
    coolest temperature rung, and still a stable answer for identical input.
    """
    if not judgments:
        return min(c["idx"] for c in survivors)
    return sorted(judgments, key=lambda j: (-j["score"], j["idx"]))[0]["idx"]


def _outcomes_path() -> Path:
    from personalclaw.config.loader import config_dir

    return Path(config_dir()) / OUTCOMES_FILENAME


def _trim_outcomes(path: Path) -> None:
    """Bound the log: rewrite to the last ``_MAX_OUTCOME_LINES`` once it doubles."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= _MAX_OUTCOME_LINES * 2:
            return
        path.write_text("\n".join(lines[-_MAX_OUTCOME_LINES:]) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.debug("best_of_n: outcome trim failed", exc_info=True)


def _record_outcome(
    *,
    n: int,
    criteria: str,
    winner_idx: int | None,
    candidates: list[dict[str, Any]],
    judgments: list[dict[str, Any]],
    prompt: str,
) -> None:
    """Append the bounded outcome record. Never raises — telemetry must not be able to
    fail a sampling call that already produced an answer."""
    try:
        from personalclaw.learning.surfacing import count_tokens

        scores = [j["score"] for j in judgments]
        spread = round(max(scores) - min(scores), 4) if len(scores) >= 2 else 0.0
        # An ESTIMATE, and named as one: one_shot_completion returns text, not usage.
        # The authoritative per-call token counts are the guard's, in model_calls.jsonl;
        # this field exists so the flywheel can ask "was the N× worth it" without
        # joining two logs.
        tokens_total = sum(count_tokens(prompt) + count_tokens(c["text"]) for c in candidates)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "n": n,
            "criteria_digest": hashlib.sha256((criteria or "").encode("utf-8")).hexdigest()[:16],
            "winner_idx": winner_idx,
            "score_spread": spread,
            "tokens_total": tokens_total,
        }
        path = _outcomes_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        _trim_outcomes(path)
    except Exception:  # noqa: BLE001
        logger.debug("best_of_n: outcome record failed", exc_info=True)


async def best_of_n(
    prompt: str,
    n: int = 3,
    judge_criteria: str = "",
    use_case: str = "background",
    *,
    judge_provider_factory: Any = None,
) -> dict[str, Any]:
    """Sample ``n`` candidates in parallel, judge them, return winner + full slate.

    Args:
        prompt: The prompt every candidate answers (identical for all N — the
            variation is the temperature, not the ask).
        n: Candidate count, clamped to ``1..MAX_N``.
        judge_criteria: What "best" means for this call. Blank falls back to the
            generic criteria above (an unstated bar is still a bar, just a weaker one).
        use_case: Model axis for the samples (``"background"`` by default — sampling is
            a background-shaped spend, not an interactive chat turn).
        judge_provider_factory: Test/engine seam — the ``LLMJudge`` provider factory.
            ``None`` resolves the reasoning axis through the bridge (the loop judge's
            precedent for the same seam).

    Returns:
        ``{winner, winner_idx, candidates, judgments, judged, n, note}``. ``winner`` is
        the winning candidate's text, or ``None`` when every sample failed (``note``
        then says so). ``candidates`` is always N wide, each ``{idx, temperature, text,
        error}``; ``judgments`` carries ``{idx, score, reason, reasoning}`` per scored
        candidate. Plain JSON shapes throughout so the MCP tool, the skill and the
        HC-5 workflow template all consume one contract.
    """
    n = max(1, min(int(n or 1), MAX_N))
    temps = _temperatures(n)

    # The fan-out. create_task + gather = N calls in flight at once; a `for` loop with
    # an await inside would peak at one and cost N× wall time.
    tasks = [
        asyncio.create_task(_sample_one(prompt, i, temp, use_case)) for i, temp in enumerate(temps)
    ]
    settled = await asyncio.gather(*tasks, return_exceptions=True)

    candidates: list[dict[str, Any]] = []
    for i, res in enumerate(settled):
        if isinstance(res, BaseException):
            # _sample_one already swallows provider errors; this is the cancellation /
            # never-should-happen rail, kept so one odd task can't drop the slate.
            candidates.append(
                {
                    "idx": i,
                    "temperature": temps[i],
                    "text": "",
                    "error": f"{type(res).__name__}: {res}",
                }
            )
        else:
            candidates.append(res)

    survivors = [c for c in candidates if c["text"].strip()]
    if not survivors:
        note = f"no candidate: all {n} sampling calls failed"
        logger.warning("best_of_n: %s", note)
        _record_outcome(
            n=n,
            criteria=judge_criteria,
            winner_idx=None,
            candidates=candidates,
            judgments=[],
            prompt=prompt,
        )
        return {
            "winner": None,
            "winner_idx": None,
            "candidates": candidates,
            "judgments": [],
            "judged": False,
            "n": n,
            "note": note,
        }

    judgments = await _judge_candidates(prompt, judge_criteria, survivors, judge_provider_factory)
    winner_idx = _select_winner(survivors, judgments)
    winner = next(c["text"] for c in survivors if c["idx"] == winner_idx)
    failed = len(candidates) - len(survivors)
    if not judgments:
        note = "unjudged: the judge was unavailable — showing the lowest-temperature candidate"
    elif failed:
        note = f"{failed} of {n} candidates failed; judged the {len(survivors)} that returned"
    else:
        note = ""
    _record_outcome(
        n=n,
        criteria=judge_criteria,
        winner_idx=winner_idx,
        candidates=candidates,
        judgments=judgments,
        prompt=prompt,
    )
    return {
        "winner": winner,
        "winner_idx": winner_idx,
        "candidates": candidates,
        "judgments": judgments,
        "judged": bool(judgments),
        "n": n,
        "note": note,
    }
