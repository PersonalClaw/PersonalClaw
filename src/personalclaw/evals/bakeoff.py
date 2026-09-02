"""The model bake-off (EVALUATION-SUBSTRATE §7, ES-10).

Answers one question with the user's OWN traffic instead of a leaderboard: *for this
use case, which of these candidate models actually does the job, and at what cost?* It
samples real inputs the use case has seen, runs each candidate model over them, scores
the outputs, and writes a per-use-case recommendation the user reads and then applies
BY HAND (rebinding ``active_models.json`` in Settings → Models). It never rewrites the
routing itself — that separation is the whole point of §7: the bake-off proposes, the
human disposes, so a surprising recommendation is inspected before it changes behaviour.

Structurally this is the judge benchmark's twin (:mod:`personalclaw.evals.judge_bench`),
and deliberately so — both run a subject across an axis, invoke models through the ONE
money-spending caller, read cost from the attempt audit rather than a token count they
cannot see, and score into the shared :class:`~personalclaw.evals.matrix.CellResult` /
:func:`~personalclaw.evals.matrix.aggregate` shape. It does NOT go through
:func:`~personalclaw.evals.runner.run_matrix`: that runner exists to contain scenario
execution in an isolated child home, and a bake-off scores a raw completion, not a
scenario — the same reason ``judge_bench`` and ``retrieval_bench`` own their loops.

Input sampling has two paths, and only the first spends nothing:

* The **attempt audit** (``model_calls.jsonl``) records which models a use case ran and
  what they cost, but NOT the prompt bodies — so it seeds the candidate axis and the
  current-cost baseline, never the inputs to replay.
* **Capture** (:func:`capture_input`) is how real inputs become available, and it is OFF
  by default (``EvalsConfig.bakeoff_capture_enabled``). When on, each input is redacted
  before it touches disk, the store is size- and count-capped, and every record carries a
  timestamp so :func:`load_captured_inputs` can drop anything past the TTL. The capture
  directory is owner-only (0700) and excluded from every export/snapshot, so a redacted
  excerpt of the user's traffic never leaves the machine.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from personalclaw.atomic_write import atomic_write
from personalclaw.evals import pinning, store
from personalclaw.evals.matrix import (
    FAILED,
    PASSED,
    TRIAL_KEY,
    VERIFIER_ABSENT,
    CellResult,
    MatrixResult,
    MatrixSpec,
    aggregate,
    aggregate_by,
    expand_cells,
)

# ── capture policy (§7): bounded so an enabled flag can never grow without limit ─────────

#: How long a captured input stays eligible for a bake-off. A captured prompt is a
#: redacted excerpt of real traffic; it is scored ONCE against candidate models and then
#: has served its purpose, so it expires rather than accumulating into a durable corpus.
CAPTURE_TTL_SECS: float = 14 * 24 * 3600.0

#: The most captured inputs kept per use case. New captures past this cap drop the OLDEST,
#: so the store is a rolling window of recent traffic, not an unbounded log.
CAPTURE_MAX_PER_USE_CASE: int = 200

#: Per-record byte cap on the redacted prompt. A single pathological prompt cannot blow up
#: the capture file; the excerpt is for scoring shape, not faithful reproduction.
CAPTURE_MAX_CHARS: int = 8192

#: Recommendation verdicts (the ledger word + the card's headline).
REC_RECOMMENDED = "recommended"  # a candidate beat the current pin by the margin
REC_HOLD = "hold"  # no candidate cleared the margin — keep the current pin
REC_INSUFFICIENT = "insufficient"  # nothing scored (no verifier / all cells absent)

#: A candidate must beat the current pin's mean score by at least this margin to be
#: recommended. A hair's-breadth win is noise, not a reason to tell the user to re-pin.
DEFAULT_MARGIN: float = 0.05


# ── the one function that spends money (mirrors judge_bench.live_judge_caller) ───────────


@dataclass(frozen=True)
class BakeoffCall:
    """One candidate invocation's output and what it cost.

    ``cost_usd`` is ``None`` for "nothing priced this call", never ``0.0``: a model whose
    price is unknown must not win a cost comparison by looking free (the same rule the
    judge bench applies). ``elapsed_secs`` is always real — a clock needs no provider.
    """

    text: str
    elapsed_secs: float = 0.0
    cost_usd: float | None = None
    model: str = ""


class BakeoffCaller(Protocol):
    """Runs one prompt against one candidate model ref. Injected so a test never spends."""

    def __call__(self, prompt: str, *, model: str, use_case: str) -> Awaitable[BakeoffCall]: ...


def _audit_cost_since(started_ts: float, use_case: str) -> tuple[float | None, str]:
    """``(cost_usd, model)`` for the attempts the guard audited since ``started_ts``.

    Reads the attempt audit rather than re-deriving price from a token count the bake-off
    cannot see — the model-call guard already records ``dollars_est``/``model`` per
    attempt. A total of exactly zero comes back as ``None`` ("an honest unknown" for an
    unpriced model): carrying 0.0 forward as a real price is exactly what would make an
    unpriced model win "cheapest adequate", so it stays unknown.
    """
    try:
        from personalclaw.guardrails.audit import read_recent

        rows = [
            r
            for r in read_recent(200)
            if float(r.get("ts") or 0.0) >= started_ts and str(r.get("use_case") or "") == use_case
        ]
    except Exception:  # noqa: BLE001 - an unreadable audit means unknown cost, not a failed run
        return None, ""
    if not rows:
        return None, ""
    total = sum(float(r.get("dollars_est") or 0.0) for r in rows)
    model = str(rows[-1].get("model") or "")
    return (total if total > 0.0 else None), model


async def live_bakeoff_caller(prompt: str, *, model: str, use_case: str) -> BakeoffCall:
    """Route one prompt through the same active-models chain the chat path walks.

    ``model`` is the candidate ref for THIS cell — passed to ``one_shot_completion`` as an
    explicit override so the bake-off compares the candidates named on the axis, not
    whatever the use case currently resolves to. Wall time is measured here; cost comes
    from the attempt audit the guard writes at the bridge seam.
    """
    from personalclaw.llm_helpers import one_shot_completion

    started_ts = time.time()
    started = time.monotonic()
    text = await one_shot_completion(prompt, use_case=use_case, model=model)
    elapsed = time.monotonic() - started
    cost, resolved = _audit_cost_since(started_ts, use_case)
    return BakeoffCall(
        text=str(text or ""), elapsed_secs=elapsed, cost_usd=cost, model=resolved or model
    )


# ── sampling: audit metadata (path A) + captured bodies (path B) ─────────────────────────


@dataclass(frozen=True)
class SampledInput:
    """One input to replay across the candidate models.

    ``assertions`` are optional task-native checks (substring must-appear). When present
    the scorer is assertion-based (deterministic, no judge call); when absent a bake-off
    needs an injected scorer or the cell is honestly ``VERIFIER_ABSENT`` — a completion
    with nothing to score against is not a zero.
    """

    id: str
    prompt: str
    assertions: tuple[str, ...] = ()


def _capture_path(use_case: str) -> Path:
    # Use-case names are a closed vocabulary (guardrails.audit.CALLERS-adjacent), but a
    # slug guard keeps a surprising value from escaping the bakeoff dir via path parts.
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in use_case) or "unknown"
    return store.bakeoff_dir() / f"{safe}.jsonl"


def capture_input(use_case: str, prompt: str, *, config: object | None = None) -> bool:
    """Capture ONE real input for later bake-off scoring — redacted, capped, timestamped.

    Returns ``True`` when a record was written, ``False`` when capture is off (the common
    case: the flag is OFF by default). The prompt is redacted BEFORE it is serialised, so
    an un-redacted body never touches disk even transiently. Records past
    :data:`CAPTURE_MAX_PER_USE_CASE` drop the oldest, keeping a rolling recent window.

    ``config`` is injected for tests; production reads the live :class:`EvalsConfig`.
    """
    if not _capture_enabled(config):
        return False
    from personalclaw.security import redact

    redacted = redact(str(prompt or ""))[:CAPTURE_MAX_CHARS]
    record = {"ts": time.time(), "prompt": redacted}
    path = _capture_path(use_case)
    existing = _read_records(path)
    existing.append(record)
    # Keep the most recent window; drop the oldest beyond the cap.
    if len(existing) > CAPTURE_MAX_PER_USE_CASE:
        existing = existing[-CAPTURE_MAX_PER_USE_CASE:]
    body = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in existing)
    atomic_write(path, body)
    path.chmod(0o600)
    return True


def _capture_enabled(config: object | None) -> bool:
    cfg = config
    if cfg is None:
        try:
            from personalclaw.config.loader import AppConfig

            cfg = AppConfig.load().evals
        except Exception:  # noqa: BLE001 - an unreadable config means capture stays off
            return False
    return bool(getattr(cfg, "bakeoff_capture_enabled", False))


def _read_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn tail line is skipped, not fatal
        if isinstance(row, dict):
            out.append(row)
    return out


def load_captured_inputs(
    use_case: str, *, now: float | None = None, limit: int = 0
) -> list[SampledInput]:
    """The unexpired captured inputs for ``use_case``, oldest→newest.

    Records past :data:`CAPTURE_TTL_SECS` are dropped on read (lazy expiry — there is no
    daemon), so a stale capture from a flag left on months ago never re-enters a run.
    ``limit`` caps the count (0 = all remaining).
    """
    clock = time.time() if now is None else now
    rows = _read_records(_capture_path(use_case))
    fresh = [r for r in rows if (clock - float(r.get("ts") or 0.0)) <= CAPTURE_TTL_SECS]
    if limit > 0:
        fresh = fresh[-limit:]
    inputs: list[SampledInput] = []
    for i, r in enumerate(fresh):
        prompt = str(r.get("prompt") or "")
        if not prompt:
            continue
        inputs.append(SampledInput(id=f"{use_case}-{i:04d}", prompt=prompt))
    return inputs


def observed_models(use_case: str, *, limit: int = 1000) -> list[str]:
    """Distinct ``Provider:model`` refs the audit saw serve ``use_case``, most-recent first.

    Seeds the candidate axis and identifies the current baseline WITHOUT spending: the
    audit says what actually ran, so a bake-off can offer "compare your current models"
    as the zero-config default. Bodies are never here (the audit does not keep them) —
    that is what capture is for.
    """
    try:
        from personalclaw.guardrails.audit import read_recent

        rows = read_recent(limit)
    except Exception:  # noqa: BLE001 - an unreadable audit yields no seed, not a crash
        return []
    seen: list[str] = []
    # Walk newest→oldest so "most-recent first" holds; read_recent is oldest→newest.
    for r in reversed(rows):
        if str(r.get("use_case") or "") != use_case:
            continue
        provider = str(r.get("provider") or "")
        model = str(r.get("model") or "")
        if not model:
            continue
        ref = f"{provider}:{model}" if provider else model
        if ref not in seen:
            seen.append(ref)
    return seen


# ── scoring ──────────────────────────────────────────────────────────────────────────────

#: A scorer maps ``(input, completion_text)`` to a score in [0, 1], or ``None`` for "could
#: not score this cell" (which becomes ``VERIFIER_ABSENT`` — never a zero).
Scorer = Callable[[SampledInput, str], "float | None"]


def assertion_scorer(inp: SampledInput, text: str) -> float | None:
    """Deterministic task-native scoring: the fraction of the input's assertions present.

    ``None`` when the input carries no assertions — the honest "no verifier" signal, so a
    bake-off with neither assertions nor an injected judge scores nothing rather than
    scoring everything 1.0 and recommending on noise.
    """
    if not inp.assertions:
        return None
    haystack = str(text or "")
    hits = sum(1 for a in inp.assertions if a and a in haystack)
    return hits / len(inp.assertions)


# ── the run (mirrors judge_bench.run_judge_bench's shape) ────────────────────────────────


@dataclass
class ModelScore:
    """One candidate model's aggregate over the sampled inputs."""

    model: str
    mean_score: float | None = None
    scored_count: int = 0
    total_cost_usd: float | None = None


@dataclass
class Recommendation:
    """The per-use-case verdict the user reads, then applies by hand."""

    use_case: str
    verdict: str
    current_model: str = ""
    recommended_model: str = ""
    current_score: float | None = None
    recommended_score: float | None = None
    rationale: str = ""
    scores: list[ModelScore] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "use_case": self.use_case,
            "verdict": self.verdict,
            "current_model": self.current_model,
            "recommended_model": self.recommended_model,
            "current_score": self.current_score,
            "recommended_score": self.recommended_score,
            "rationale": self.rationale,
            "scores": [vars(s) for s in self.scores],
        }


@dataclass
class BakeoffResult:
    """A whole bake-off: the spec, every cell, the aggregates, and the recommendation."""

    bench_id: str
    spec: MatrixSpec
    cells: list[CellResult]
    aggregates: dict
    recommendation: Recommendation

    def to_matrix_result(self) -> MatrixResult:
        return MatrixResult(spec=self.spec, cells=self.cells, aggregates=self.aggregates)


def new_bakeoff_id(use_case: str) -> str:
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in use_case) or "unknown"
    return f"bakeoff-{safe}-{stamp}"


def _inputs_sha(inputs: list[SampledInput]) -> str:
    h = sha256()
    for inp in inputs:
        h.update(inp.id.encode("utf-8"))
        h.update(b"\x00")
        h.update(inp.prompt.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


async def run_bakeoff(
    use_case: str,
    *,
    models: list[str],
    inputs: list[SampledInput],
    caller: BakeoffCaller | None = None,
    scorer: Scorer | None = None,
    current_model: str = "",
    budget_usd: float = 0.0,
    trial_count: int = 1,
    margin: float = DEFAULT_MARGIN,
    bench_id: str = "",
) -> BakeoffResult:
    """Run every ``(model × input)`` cell, score the outputs, and persist the recommendation.

    Cells run SEQUENTIALLY (single-user machine; the model calls are the cost). The pin is
    computed FIRST and refused if incomplete, so a run whose result could never enter the
    ledger does not burn spend — the rule ``run_matrix`` and ``run_judge_bench`` share.
    ``budget_usd`` is a hard cap: once cumulative audited cost exceeds it, the remaining
    cells are marked ``VERIFIER_ABSENT`` rather than spawned, so a run cannot overspend.

    ``caller`` and ``scorer`` are injected; production defaults route through the live
    active-models chain and the deterministic assertion scorer.
    """
    if not models:
        raise ValueError("run_bakeoff needs at least one candidate model")
    if not inputs:
        raise ValueError("run_bakeoff needs at least one sampled input")
    call = caller or live_bakeoff_caller
    score_fn = scorer or assertion_scorer
    bench_id = bench_id or new_bakeoff_id(use_case)

    pin = pinning.compute_pin_for_subject(f"bakeoff:{use_case}", _inputs_sha(inputs))
    if not pin.is_complete():
        raise store.PinRequiredError(
            f"refusing to run bakeoff {bench_id}: incomplete RunPin "
            f"(missing: {', '.join(pin.missing_parts())})"
        )

    spec = MatrixSpec(
        subject=f"bakeoff:{use_case}",
        axes={"model": list(models), "input": [i.id for i in inputs]},
        trial_count=max(1, int(trial_count)),
        scorer="assertion" if scorer is None else "custom",
        budget_usd=budget_usd,
    )
    bench_dir = store.matrix_dir(bench_id)
    store.write_matrix_experiment(bench_id, spec.to_dict())
    pinning.write_pin(bench_dir, pin)

    by_id = {i.id: i for i in inputs}
    spent = 0.0
    cells: list[CellResult] = []
    for combo in expand_cells(spec):
        coords = {k: v for k, v in combo.items() if k != TRIAL_KEY}
        model = str(coords.get("model") or "")
        inp = by_id.get(str(coords.get("input") or ""))
        if inp is None:  # pragma: no cover - ids come from the inputs themselves
            continue
        if budget_usd > 0.0 and spent >= budget_usd:
            # The cap is reached: record the cell as unscored rather than overspending.
            cells.append(CellResult(coords=coords, outcome=VERIFIER_ABSENT))
            continue
        try:
            result = await call(inp.prompt, model=model, use_case=use_case)
        except Exception:  # noqa: BLE001 - a provider fault is an absent sample, not a run failure
            cells.append(CellResult(coords=coords, outcome=VERIFIER_ABSENT))
            continue
        if result.cost_usd is not None:
            spent += float(result.cost_usd)
        score = score_fn(inp, result.text)
        if score is None:
            cells.append(CellResult(coords=coords, outcome=VERIFIER_ABSENT))
            continue
        outcome = PASSED if score > 0.0 else FAILED
        cells.append(CellResult(coords=coords, outcome=outcome, score=float(score)))

    aggregates = aggregate(cells)
    recommendation = recommend(use_case, cells, current_model=current_model, margin=margin)
    store.write_matrix_aggregates(bench_id, aggregates)
    store.write_matrix_trials(bench_id, cells)
    _write_recommendation(bench_id, recommendation)
    store.append_result(
        {
            "study_id": bench_id,
            "kind": "bakeoff",
            "verdict": recommendation.verdict,
            "score_new": aggregates.get("mean_score"),
            "k": spec.trial_count,
            "ts": datetime.now(tz=timezone.utc).isoformat(),
        },
        pin=pin,
    )
    return BakeoffResult(
        bench_id=bench_id,
        spec=spec,
        cells=cells,
        aggregates=aggregates,
        recommendation=recommendation,
    )


def recommend(
    use_case: str,
    cells: list[CellResult],
    *,
    current_model: str = "",
    margin: float = DEFAULT_MARGIN,
) -> Recommendation:
    """Pick the best-scoring model, but only RECOMMEND it if it clears the margin.

    The verdict is deliberately conservative: a candidate that only ties (or barely beats)
    the current pin yields ``hold``, because telling the user to re-pin on noise is worse
    than staying put. ``insufficient`` when nothing scored at all — the bake-off ran but
    had no verifier, which the card must say rather than inventing a winner.
    """
    per_model = aggregate_by(cells, "model")
    scores: list[ModelScore] = []
    for model, agg in per_model.items():
        if not model:
            continue
        scores.append(
            ModelScore(
                model=model,
                mean_score=agg.get("mean_score"),
                scored_count=int(agg.get("scored_count") or 0),
            )
        )
    scores.sort(key=lambda s: (s.mean_score if s.mean_score is not None else -1.0), reverse=True)

    scored = [s for s in scores if s.mean_score is not None]
    if not scored:
        return Recommendation(
            use_case=use_case,
            verdict=REC_INSUFFICIENT,
            current_model=current_model,
            rationale="No cell scored — the bake-off had no verifier for these inputs.",
            scores=scores,
        )

    best = scored[0]
    current = next((s for s in scores if s.model == current_model), None)
    current_score = current.mean_score if current else None
    baseline = current_score if current_score is not None else 0.0

    if best.model != current_model and (best.mean_score or 0.0) - baseline >= margin:
        if current_score is not None:
            versus = f"current {current_model} {current_score:.2f}"
        else:
            versus = "no current baseline"
        rationale = (
            f"{best.model} scored {best.mean_score:.2f} vs {versus} "
            f"(margin ≥ {margin:.2f}). Rebind active_models.json by hand to adopt it."
        )
        return Recommendation(
            use_case=use_case,
            verdict=REC_RECOMMENDED,
            current_model=current_model,
            recommended_model=best.model,
            current_score=current_score,
            recommended_score=best.mean_score,
            rationale=rationale,
            scores=scores,
        )

    return Recommendation(
        use_case=use_case,
        verdict=REC_HOLD,
        current_model=current_model,
        recommended_model=current_model or best.model,
        current_score=current_score,
        recommended_score=best.mean_score,
        rationale=(f"No candidate beat the current pin by ≥ {margin:.2f}; keep the current model."),
        scores=scores,
    )


def _write_recommendation(bench_id: str, rec: Recommendation) -> None:
    """Persist the recommendation as machine-readable JSON + a human-readable Markdown row.

    Two files, like the other benches' ``table.json``/``table.tsv``: the JSON is the record
    a later reader parses, the Markdown is what the user actually reads before deciding
    whether to re-pin.
    """
    d = store.matrix_dir(bench_id)
    atomic_write(
        d / "recommendation.json",
        json.dumps(rec.to_dict(), indent=2, sort_keys=True) + "\n",
    )
    atomic_write(d / "recommendation.md", _render_recommendation_md(rec))


def _render_recommendation_md(rec: Recommendation) -> str:
    lines = [
        f"# Model bake-off — `{rec.use_case}`",
        "",
        f"**Verdict:** {rec.verdict}",
        "",
        rec.rationale,
        "",
        "| model | mean score | scored cells |",
        "| --- | --- | --- |",
    ]
    for s in rec.scores:
        score = "—" if s.mean_score is None else f"{s.mean_score:.2f}"
        lines.append(f"| `{s.model}` | {score} | {s.scored_count} |")
    lines.append("")
    lines.append(
        "> Applying a recommendation is manual: edit `active_models.json` "
        "(Settings → Models). The bake-off proposes; it never re-pins for you."
    )
    return "\n".join(lines) + "\n"
