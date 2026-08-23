"""Routing feedback extraction — WF2 judge verdicts as a per-cell signal (MODEL-ROUTING-TELEMETRY
§4.2, MRT-5).

:func:`personalclaw.routing.stats._score` already computes ``0.60·success_rate + 0.40·feedback`` and
already renormalizes onto ``success_rate`` when ``feedback_n`` is 0. This module supplies the
missing INPUT — the ``(feedback, feedback_n)`` pair per ``(use_case, query_class, ref)`` cell — and
deliberately does not re-derive the arithmetic or the fallback: a second renormalisation that
disagreed with ``_score``'s would be two answers to one question, which is the duplication the
ledger extraction (PP-4) exists to prevent.

── What is read ──

The WF2 Run Ledger: ``<home>/workflows/runs/<run_id>/events.jsonl``, the file
:class:`personalclaw.ledger.writer.LedgerWriter` mirrors every :data:`LEDGER_KINDS` record into.
The kind is :data:`personalclaw.ledger.kinds.JUDGE_VERDICT` (``"judge_verdict"``) — "something
assessed something" — and the field read off it is ``verdict``, the closed
:class:`personalclaw.workflows.judge_contract.Verdict` vocabulary reconciled by ``WF2LOO-16``.
Nothing else is consulted: no second store, no parallel signal.

── The attribution rule, and the measured gap it exists to state ──

A judge verdict is usable as routing feedback only if it can be attributed to a
``(use_case, query_class, ref)`` triple, so an event must carry all three ITSELF: ``use_case``,
``query_class``, and either ``ref`` or both ``provider`` and ``model``. Anything short of that is
DROPPED (counted in a debug census, never guessed at).

**Measured on `main` at 12469c65, and the reason this reads as a strict requirement rather than a
join:** no ledger event carries the triple today.

* ``judge_verdict`` from the workflow producer (``workflows/controller.py``, at the gate settle)
  carries ``instance_path``, ``node_id``, ``epoch``, ``template``, ``verdict``, ``status``,
  ``evidence`` — no model, no provider, no use case, no query class.
* ``judge_verdict`` from the loop producer (``loop/journal.py``) carries ``cycle`` plus
  ``JudgeVerdict.to_dict()`` — same three coordinates missing.
* ``step_completed`` DOES carry ``model``/``provider``, but joining a verdict to it on ``node_id``
  resolves to the JUDGE GATE'S OWN model (the controller stamps the gate node's id), not the model
  whose output was judged. Attributing a PASS to the model that issued it would make a lenient
  judge look like a strong worker, so that join is refused rather than taken.
* ``query_class`` is computed only in ``guardrails/model_call.py`` at call time and lands only in
  ``model_calls.jsonl``; there is no ``audit_id`` on a ledger event and no run/node coordinate on
  an attempt row, so there is no key on which the two records could be joined either. This is the
  same "no join key" finding ``routing/usage.py`` (MRT-3) recorded for spend, and the same answer:
  fold what is attributable, census what is not, state the gap.

So the signal is 0 with ``feedback_n: 0`` in practice until a producer stamps those three fields on
the event it already writes, at which point this reader picks them up with no change here. That is
exactly the shape MRT-5's EXT dependency declares SOFT: the router functions on
``model_calls.jsonl`` alone, and ``_score`` collapses onto ``success_rate``.

── The scale, and the two refusals ──

``_score`` consumes ``feedback`` on the same [0,1] scale as ``success_rate`` (see the fold's
"EMA of a [0,1] signal"). Only two members of the closed vocabulary are assessments OF THE JUDGED
OUTPUT, so only they map onto it:

* ``PASS`` → 1.0 — an independent judge accepted the output.
* ``REJECT`` → 0.0 — it did not.

Everything else is refused, and the two refusals are different:

* ``RETRY``/``REPLAN``/``ESCALATE``/``NEEDS_INPUT`` are CONTROL FLOW, not quality: ``RETRY`` names a
  transient hiccup and ``ESCALATE``/``NEEDS_INPUT`` name "a human must decide". Scoring a transient
  as 0.0 would penalize a model for a network blip.
* A verdict string outside the closed enum entirely is a PARSER GAP. It is dropped, not scored
  neutral and not scored bad: a 0.5 nobody chose is a fabricated number steering real routing, and
  a 0.0 punishes a model for this module failing to keep up with the vocabulary. Dropping keeps
  ``feedback_n`` an honest count and makes the gap visible in the census instead.

``cannot_judge`` is likewise dropped — a refusal that says why is still not an assessment.

Note that ``PASS``/``REJECT`` is NOT a restatement of ``success_rate``: that is ``passed`` on the
attempt row (did the call mechanically succeed), while this is whether an independent judge accepted
what the call produced. A model that answers fast and wrongly scores 1.0 on the first and 0.0 here.

``quality_score`` (0-5) is deliberately NOT used even though the loop dialect carries it, because
the workflow producer does not: preferring it where present would make two producers' feedback
numbers non-comparable inside one cell — a fifth dialect, in the shape the vocabulary
reconciliation exists to stop.

── What increments ``feedback_n`` ──

Exactly one thing: an attributable event carrying ``PASS`` or ``REJECT``, counted once per
``event_id``. Not an unattributable event, not a control-flow verdict, not an unknown verdict, not
``cannot_judge``, and not a re-read of an ``event_id`` already counted (``event_id`` is
deterministic — ``<run>-evt-<seq>`` — so a re-emit is idempotent here too). ``feedback_n``
feeds an ``n >= 5`` decision floor downstream, so an inflated count changes routing on nothing.

── Never raises, never writes ──

A missing home, a missing ledger, a corrupt line and a half-written final line all read as "no
signal". Nothing here opens a file for writing. A feedback-read failure must not break a routing
decision, for the same reason ``stats.py`` states about the fold: the model call is the product,
this is observability.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterator

from personalclaw.ledger.kinds import JUDGE_VERDICT
from personalclaw.ledger.writer import EVENTS_FILE
from personalclaw.routing.stats import ref_of

logger = logging.getLogger(__name__)

#: Where the WF2 run ledgers live, relative to the home. Mirrors
#: :func:`personalclaw.workflows.store.runs_root` — asserted equal by
#: ``test_runs_root_matches_the_workflows_store_layout``, rather than importing it, because
#: ``workflows`` reaches ``routing`` through ``provider_bridge`` and a module-level import back
#: would be a cycle. The store's own helper cannot be used regardless: it resolves through
#: ``config_dir()`` and so cannot be pointed at a caller-supplied ``home``.
_RUNS_SUBPATH = ("workflows", "runs")

#: The two verdicts that assess the judged OUTPUT, on ``_score``'s [0,1] scale.
_QUALITY_FEEDBACK: dict[str, float] = {"PASS": 1.0, "REJECT": 0.0}

#: The closed vocabulary, so an unknown string is distinguishable from a known control-flow
#: member. Asserted equal to ``judge_contract.Verdict``'s members by
#: ``test_closed_verdict_vocabulary_matches_judge_contract`` — restated rather than imported for
#: the same cycle reason as :data:`_RUNS_SUBPATH`.
_KNOWN_VERDICTS = frozenset({"PASS", "REJECT", "RETRY", "REPLAN", "ESCALATE", "NEEDS_INPUT"})

#: The cell key: ``(use_case, query_class, ref)`` — the same shape ``stats.fold_record`` keys on.
Cell = tuple[str, str, str]


def _resolve_home(home: Path | None) -> Path | None:
    """The caller's home, or the configured one. ``None`` when neither exists — which reads as
    "no signal", never as an error (mirrors ``rates._resolve_home`` / ``policy._default_home``)."""
    if home is not None:
        return Path(home)
    try:
        from personalclaw.config.loader import config_dir

        return Path(config_dir())
    except Exception:  # noqa: BLE001 — no home configured is not a routing failure
        return None


def _events_files(home: Path) -> list[Path]:
    """Every run's ``events.jsonl``, in a stable order. A missing runs root is no signal."""
    root = Path(home).joinpath(*_RUNS_SUBPATH)
    try:
        run_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    except (FileNotFoundError, NotADirectoryError, OSError):
        return []
    return [d / EVENTS_FILE for d in run_dirs]


def _read_records(path: Path) -> Iterator[dict[str, Any]]:
    """Tolerant JSONL read — the reader ``store.read_jsonl`` already is.

    A corrupt line is skipped and a half-written final line is skipped, because a process killed
    mid-append is expected: refusing the whole file would lose every signal it holds over one
    partial tail.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, IsADirectoryError, OSError):
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            yield rec


def _cell_of(event: dict[str, Any]) -> Cell | None:
    """The ``(use_case, query_class, ref)`` this event is attributable to, or ``None``.

    All three coordinates must be ON the event. ``ref`` may be spelled directly or as
    ``provider`` + ``model`` (joined by :func:`stats.ref_of`, never formatted here — a
    colon-bearing model id like ``gpt-oss:20b`` has exactly one correct spelling). A half ref
    (a provider with no model, or the reverse) is not a ref.
    """
    use_case = str(event.get("use_case") or "")
    query_class = str(event.get("query_class") or "")
    if not use_case or not query_class:
        return None
    ref = str(event.get("ref") or "")
    if not ref:
        provider = str(event.get("provider") or "")
        model = str(event.get("model") or "")
        if not provider or not model:
            return None
        ref = ref_of(provider, model)
    return (use_case, query_class, ref)


def feedback_index(*, home: Path | None = None) -> dict[Cell, tuple[float, int]]:
    """One pass over the ledger → ``{(use_case, query_class, ref): (feedback, feedback_n)}``.

    Cells with no counted observation are ABSENT rather than present as ``(0.0, 0)``: the learned
    stage scores many cells at once and asks this for each, and an entry claiming a zero-sample
    reading is indistinguishable from one claiming a measured 0.0 feedback.
    """
    resolved = _resolve_home(home)
    if resolved is None:
        return {}
    totals: dict[Cell, list[float]] = {}
    counted_ids: set[str] = set()
    dropped = {
        "unattributed": 0,
        "control_flow": 0,
        "unknown_verdict": 0,
        "cannot_judge": 0,
        "duplicate": 0,
    }
    for path in _events_files(resolved):
        for event in _read_records(path):
            if event.get("kind") != JUDGE_VERDICT:
                continue
            event_id = str(event.get("event_id") or "")
            if event_id and event_id in counted_ids:
                dropped["duplicate"] += 1
                continue
            cell = _cell_of(event)
            if cell is None:
                dropped["unattributed"] += 1
                continue
            if event.get("cannot_judge"):
                dropped["cannot_judge"] += 1
                continue
            verdict = str(event.get("verdict") or "").strip().upper()
            if verdict not in _QUALITY_FEEDBACK:
                key = "control_flow" if verdict in _KNOWN_VERDICTS else "unknown_verdict"
                dropped[key] += 1
                continue
            if event_id:
                counted_ids.add(event_id)
            acc = totals.setdefault(cell, [0.0, 0.0])
            acc[0] += _QUALITY_FEEDBACK[verdict]
            acc[1] += 1.0
    if any(dropped.values()):
        # A census, not an error: the gap this module was built to state is only visible here.
        logger.debug("routing feedback: dropped %s", dropped)
    return {cell: (round(total / n, 4), int(n)) for cell, (total, n) in totals.items()}


def feedback_for(
    use_case: str,
    query_class: str,
    ref: str,
    *,
    home: Path | None = None,
) -> tuple[float, int]:
    """``(feedback, feedback_n)`` for one cell — ``(0.0, 0)`` when there is no signal.

    Delegates to :func:`feedback_index` by construction. Two implementations of one arithmetic
    would drift, and the per-cell one is the one a reader would trust while the many-cell one is
    the one the learned stage actually runs. The cost is a full pass for a single cell, which is
    the right trade: this is the single-cell convenience, and a caller scoring N cells is the
    reason :func:`feedback_index` exists.
    """
    return feedback_index(home=home).get((use_case, query_class, ref), (0.0, 0))
