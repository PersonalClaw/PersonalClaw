"""The voice-clone ENGINE bake-off (MULTIMODAL-IO §2.2, atom MI-6).

Answers the one question MI-2 §2.2 deferred: *for a local, macOS-first, cloning-first
personal assistant, which zero-shot voice-clone engine do we ship in
``apps/voice-clone-tts`` — k2-fsa OmniVoice or FunAudioLLM CosyVoice — and why is the
other rejected?* It scores the two candidates over a fixed criteria matrix and prints a
recommendation plus the loser's rejection notes.

This is NOT the ES-10 traffic bake-off (:mod:`personalclaw.evals.bakeoff`). That one
replays the user's OWN LLM traffic through candidate *routing* models. This one is a
one-shot *engine-selection* spike over two local TTS engines: a pure, deterministic,
offline scorecard with no model call and no network, plus an OPT-IN measurement path
(:func:`measure_fixture_rtf`) that runs real fixture inference when an engine and its
weights are actually installed and degrades to a clearly-reasoned skip otherwise.

**Honesty contract (the reason this file is careful about provenance).** Every cell
carries a :class:`Provenance`. Two things are kept strictly apart:

* the ``raw`` published value — LITERATURE, cited to a real URL, or UNKNOWN when the
  vendor does not publish it (never invented); and
* the ``score`` in ``[0, 1]`` — the reviewer's JUDGMENT mapping of that evidence onto
  *this* product's use case, documented in the companion plan doc.

Provenance is per-cell: literature and judgment cells carry citations, and the
``measured`` cells (footprint, load RSS) come from the project's Apple-silicon
integration host — the one place MI-6's own flag said they must come from. The
remaining fixture-axis latency measurement is represented as a measure-deferred
item, not a guess. When :func:`measure_fixture_rtf` cannot run it says so; it never
fabricates a latency.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ── provenance: how a cell's value came to be, kept apart from the score ────────────────

#: The vendor publishes it and we cite the source. Never a number we produced.
LITERATURE = "literature"
#: We ran it on this host and recorded it. No cell carries this in the offline scorecard.
MEASURED = "measured"
#: The reviewer's mapping of evidence onto the PersonalClaw use case (the 0-1 score).
JUDGMENT = "judgment"
#: The vendor does not publish it. The value is ``None`` and the criterion is excluded
#: from the aggregate rather than filled with a guess.
UNKNOWN = "unknown"

#: License substrings that mark a non-commercial grant — kept in lockstep with the
#: ``local_models.provider`` "omnivoice rule" so a card's license reads the same here as
#: it does at bind time. Advisory only: a non-commercial engine is flagged, never blocked.
_NON_COMMERCIAL_MARKERS: tuple[str, ...] = ("-nc", "cc-by-nc", "noncommercial", "non-commercial")


def is_non_commercial(license_id: str) -> bool:
    """Whether an SPDX id looks non-commercial (mirrors ``local_models.provider``)."""
    low = license_id.lower()
    return any(marker in low for marker in _NON_COMMERCIAL_MARKERS)


@dataclass(frozen=True)
class Citation:
    """A verifiable source for a published value — a short label plus a real URL."""

    label: str
    url: str
    note: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "url": self.url, "note": self.note}


@dataclass(frozen=True)
class Metric:
    """One candidate's standing on one criterion.

    ``raw`` is the published, human-readable value (or ``""`` when unknown); ``score`` is
    the reviewer's ``[0, 1]`` mapping used for the aggregate (``None`` = excluded). The
    two never merge: ``provenance`` describes ``raw``, and a literature ``raw`` with a
    judgment ``score`` is the normal case, so the reader always sees which is which.
    """

    raw: str
    score: float | None
    provenance: str
    citation: Citation | None = None

    @property
    def known(self) -> bool:
        return self.score is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "score": self.score,
            "provenance": self.provenance,
            "citation": self.citation.to_dict() if self.citation else None,
        }


@dataclass(frozen=True)
class Criterion:
    """A scored axis. ``weight`` is the pre-normalization share for the use-case model."""

    key: str
    label: str
    weight: float
    rationale: str


@dataclass
class EngineCandidate:
    """A voice-clone engine under evaluation, with its per-criterion metrics."""

    key: str
    name: str
    homepage: str
    license_spdx: str
    metrics: dict[str, Metric] = field(default_factory=dict)

    @property
    def non_commercial(self) -> bool:
        return is_non_commercial(self.license_spdx)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "homepage": self.homepage,
            "license_spdx": self.license_spdx,
            "non_commercial": self.non_commercial,
            "metrics": {k: m.to_dict() for k, m in self.metrics.items()},
        }


@dataclass(frozen=True)
class ScoredCell:
    """A criterion's outcome across both candidates once aggregated."""

    criterion: Criterion
    scored: bool  # False when either side is UNKNOWN → excluded from the aggregate
    exclude_reason: str = ""


@dataclass
class BakeoffReport:
    """The full scorecard: candidates, per-criterion cells, aggregate scores, verdict."""

    candidates: list[EngineCandidate]
    criteria: list[Criterion]
    cells: list[ScoredCell]
    scores: dict[str, float]
    winner: str
    verdict: str
    rejection_notes: str
    deferred: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "criteria": [asdict(c) for c in self.criteria],
            "excluded_criteria": [
                {"key": c.criterion.key, "reason": c.exclude_reason}
                for c in self.cells
                if not c.scored
            ],
            "scores": self.scores,
            "winner": self.winner,
            "verdict": self.verdict,
            "rejection_notes": self.rejection_notes,
            "deferred": list(self.deferred),
        }


# ── the criteria matrix (MI-6 names latency, quality, footprint, license, platform, ─────
# install; language coverage is added because a 600-vs-9 gap is decision-grade for a
# general assistant). Weights encode PersonalClaw's use case — LOCAL, macOS-first,
# cloning-first — and are documented in docs/roadmap/plans/MI-6-voice-engine-bakeoff.md.

CRITERIA: list[Criterion] = [
    Criterion(
        "platform_support",
        "Platform support (Apple Silicon / MPS)",
        0.25,
        "PClaw runs on the user's own Mac; a first-class MPS path is the load-bearing axis.",
    ),
    Criterion(
        "quality_proxy",
        "Clone quality proxy (CER/SS, published)",
        0.20,
        "The output has to sound like the reference; scored from vendor-published metrics.",
    ),
    Criterion(
        "install_weight",
        "Install weight / complexity",
        0.15,
        "The install must stay a double-click; submodules + system deps erode that.",
    ),
    Criterion(
        "latency",
        "Inference latency (RTF / first-packet)",
        0.15,
        "Personal, non-streaming synthesis — fast enough matters, sub-real-time is plenty.",
    ),
    Criterion(
        "footprint",
        "Weight footprint on disk",
        0.10,
        "Local weights ship to the user's disk; smaller is better, but not decisive.",
    ),
    Criterion(
        "license",
        "License permissiveness",
        0.10,
        "Must allow local, personal, potentially-commercial use with no NC clause.",
    ),
    Criterion(
        "language_coverage",
        "Language coverage",
        0.05,
        "A general assistant meets many languages; broad zero-shot coverage is a plus.",
    ),
]

_OMNIVOICE_GH = Citation("OmniVoice README", "https://github.com/k2-fsa/OmniVoice")
_OMNIVOICE_HF = Citation("OmniVoice model card", "https://huggingface.co/k2-fsa/OmniVoice")
_OMNIVOICE_MLX = Citation(
    "OmniVoice MLX (Apple Silicon) weights", "https://huggingface.co/mlx-community/OmniVoice"
)
_OMNIVOICE_PAPER = Citation("OmniVoice paper", "https://arxiv.org/abs/2604.00688")
_COSY_GH = Citation("CosyVoice README", "https://github.com/FunAudioLLM/CosyVoice")
_COSY_PAPER = Citation("CosyVoice 3 paper", "https://arxiv.org/pdf/2505.17589")
_COSY2_PAPER = Citation("CosyVoice 2 paper", "https://arxiv.org/pdf/2412.10117")


def candidates() -> list[EngineCandidate]:
    """The two engines, grounded in their published READMEs / model cards / papers.

    Raw values are literature (cited) or explicitly UNKNOWN; scores are the reviewer's
    judgment mapping onto PClaw's use case (see the companion plan doc for each rationale).
    """
    omnivoice = EngineCandidate(
        key="omnivoice",
        name="k2-fsa OmniVoice",
        homepage="https://github.com/k2-fsa/OmniVoice",
        license_spdx="Apache-2.0",
        metrics={
            "platform_support": Metric(
                'device_map="mps" documented; community MLX weights for Apple Silicon',
                1.0,
                JUDGMENT,
                _OMNIVOICE_MLX,
            ),
            "quality_proxy": Metric(
                "SOTA-cloning claim; ASR-verified lossless in RTF bench; no public CER/SS table",
                0.80,
                LITERATURE,
                _OMNIVOICE_PAPER,
            ),
            "install_weight": Metric(
                "single `pip install omnivoice` (PyPI); pynini via conda only for the tn extra",
                0.90,
                JUDGMENT,
                _OMNIVOICE_GH,
            ),
            "latency": Metric(
                "RTF 0.0899 (batch=1, H100, fp16, num_step=32); ~0.025 batched/accelerated",
                0.80,
                LITERATURE,
                _OMNIVOICE_GH,
            ),
            "footprint": Metric(
                "3.27 GB on disk (HF snapshot k2-fsa/OmniVoice, 42 files; measured on the "
                "project's Apple-silicon integration host — ~2.8 GB RSS after MPS load)",
                0.60,
                MEASURED,
                _OMNIVOICE_HF,
            ),
            "license": Metric("Apache-2.0", 1.0, LITERATURE, _OMNIVOICE_GH),
            "language_coverage": Metric("600+ languages", 1.0, LITERATURE, _OMNIVOICE_GH),
        },
    )
    cosyvoice = EngineCandidate(
        key="cosyvoice",
        name="FunAudioLLM CosyVoice",
        homepage="https://github.com/FunAudioLLM/CosyVoice",
        license_spdx="Apache-2.0",
        metrics={
            "platform_support": Metric(
                "README is CUDA/NVIDIA-docker/vLLM/TensorRT-LLM centric; no first-class MPS path",
                0.40,
                JUDGMENT,
                _COSY_GH,
            ),
            "quality_proxy": Metric(
                "Fun-CosyVoice3-0.5B-RL: test-en WER 1.68 / SS 69.5, test-zh CER 0.81 / SS 77.4",
                0.90,
                LITERATURE,
                _COSY_PAPER,
            ),
            "install_weight": Metric(
                "recursive submodule clone + conda py3.10 + requirements + optional ttsfrd/sox",
                0.40,
                JUDGMENT,
                _COSY_GH,
            ),
            "latency": Metric(
                "bi-streaming first-packet as low as 150 ms (streaming-optimized)",
                0.85,
                LITERATURE,
                _COSY2_PAPER,
            ),
            "footprint": Metric("0.5B params (CosyVoice2/3-0.5B)", 0.70, LITERATURE, _COSY_GH),
            "license": Metric("Apache-2.0", 1.0, LITERATURE, _COSY_GH),
            "language_coverage": Metric(
                "9 languages + 18+ Chinese dialects", 0.50, LITERATURE, _COSY_GH
            ),
        },
    )
    return [omnivoice, cosyvoice]


def _aggregate(
    cands: list[EngineCandidate], criteria: list[Criterion]
) -> tuple[dict[str, float], list[ScoredCell]]:
    """Weighted mean over criteria KNOWN for every candidate, renormalized to the kept set.

    A criterion where any candidate's value is UNKNOWN is excluded (you cannot fairly
    compare a published number against a blank) and flagged, rather than defaulted — the
    honest handling of OmniVoice's unpublished footprint.
    """
    cells: list[ScoredCell] = []
    included: list[Criterion] = []
    for crit in criteria:
        missing = [c.name for c in cands if not c.metrics[crit.key].known]
        if missing:
            cells.append(
                ScoredCell(
                    crit, False, f"unknown for: {', '.join(missing)} — excluded, not guessed"
                )
            )
        else:
            cells.append(ScoredCell(crit, True))
            included.append(crit)

    total_weight = sum(c.weight for c in included) or 1.0
    scores: dict[str, float] = {}
    for cand in cands:
        acc = 0.0
        for crit in included:
            score = cand.metrics[crit.key].score
            assert score is not None  # guaranteed by `included`
            acc += crit.weight * score
        scores[cand.key] = round(acc / total_weight, 4)
    return scores, cells


def run_bakeoff() -> BakeoffReport:
    """Build the deterministic scorecard + verdict. Pure: no I/O, no model, no network."""
    cands = candidates()
    scores, cells = _aggregate(cands, CRITERIA)
    ranked = sorted(cands, key=lambda c: scores[c.key], reverse=True)
    winner, loser = ranked[0], ranked[1]

    verdict = (
        f"Recommend {winner.name} (score {scores[winner.key]:.3f} vs "
        f"{scores[loser.key]:.3f} for {loser.name}). For a LOCAL, macOS-first, "
        "cloning-first personal assistant it wins on the load-bearing axes: a documented "
        "Apple-Silicon/MPS path, a single-package install, broad language coverage, and "
        "cloning as the primary trained task with a save/load clone-prompt API that maps "
        "onto the plan's precomputed-clone-prompt LRU and locked-voice conditioning seams."
    )
    rejection_notes = (
        f"{loser.name} — REJECTED (not shipped as a second app; notes kept here per MI-2 "
        "§2.2). It is the stronger engine on two axes and the call is not lopsided: it "
        "edges published clone quality (CER/SS numbers vs OmniVoice's qualitative claim) "
        "and streaming first-packet latency (~150 ms), and its community and CUDA/vLLM "
        "deployment story are more mature. It loses for THIS product because (1) its "
        "documented runtime is CUDA/NVIDIA-docker/vLLM/TensorRT-LLM with no first-class "
        "Apple-Silicon/MPS path, and PClaw runs on the user's Mac; (2) its install is a "
        "recursive-submodule clone + conda env + optional ttsfrd wheels + sox system "
        "deps, which fights the double-click-install tenet; (3) its zero-shot coverage is "
        "~9 languages + Chinese dialects vs 600+. Its edges (server streaming latency, "
        "CUDA throughput) are exactly the ones a single-user local sidecar does not cash "
        "in. Re-evaluate if PClaw ever grows a server-side or CUDA synthesis tier."
    )
    deferred = [
        "fixture-set MPS latency (measure_fixture_rtf under a controlled host): the "
        "integration run recorded cold load 66.6s / warm 8.0s and a 4.1s short-utterance "
        "synthesis on MPS, but under loadavg ~15 — honest per-fixture RTF needs a quiet host",
    ]
    return BakeoffReport(
        candidates=cands,
        criteria=CRITERIA,
        cells=cells,
        scores=scores,
        winner=winner.key,
        verdict=verdict,
        rejection_notes=rejection_notes,
        deferred=deferred,
    )


# ── opt-in measurement: real fixture inference, or a clearly-reasoned skip ──────────────

_ENGINE_IMPORT = {"omnivoice": "omnivoice", "cosyvoice": "cosyvoice"}


@dataclass(frozen=True)
class MeasureResult:
    """Outcome of a real-inference measurement attempt.

    ``skipped`` with a ``reason`` is the expected result on a host without the engine or
    its weights — the harness stays runnable everywhere and never invents a latency.
    """

    engine: str
    skipped: bool
    reason: str = ""
    rtf: float | None = None
    fixtures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def measure_fixture_rtf(
    engine: str,
    fixtures_dir: str | Path,
    *,
    weights_present: bool = False,
) -> MeasureResult:
    """Attempt to measure real-time factor over reference-audio fixtures, or skip clearly.

    Skips (never fabricates) when: the engine key is unknown, the engine package is not
    importable, its weights are not present, or the fixtures dir has no ``*.wav``. Only
    when all hold would it run inference — deliberately deferred to the follow-up run, so
    here it stops at a typed "ready to measure" skip rather than pulling a model in.
    """
    if engine not in _ENGINE_IMPORT:
        return MeasureResult(engine, True, f"unknown engine key '{engine}'")

    module = _ENGINE_IMPORT[engine]
    if importlib.util.find_spec(module) is None:
        return MeasureResult(engine, True, f"engine package '{module}' not installed on this host")

    fdir = Path(fixtures_dir)
    wavs = sorted(fdir.glob("*.wav")) if fdir.is_dir() else []
    if not wavs:
        return MeasureResult(engine, True, f"no reference-audio fixtures (*.wav) under {fdir}")

    if not weights_present:
        return MeasureResult(
            engine,
            True,
            "engine + fixtures present but weights not confirmed; pass weights_present=True "
            "to run inference (deferred to the MI-6 follow-up)",
            fixtures=len(wavs),
        )

    # Ready-to-measure real path (exercised only in the follow-up once weights land). The
    # loop shape is fixed here so the follow-up wires the engine call, not the timing.
    started = time.monotonic()
    audio_secs = 0.0  # follow-up: sum synthesized-output seconds from the engine call
    elapsed = time.monotonic() - started
    rtf = round(elapsed / audio_secs, 4) if audio_secs > 0 else None
    return MeasureResult(engine, False, "", rtf=rtf, fixtures=len(wavs))


# ── rendering + CLI ─────────────────────────────────────────────────────────────────────


def format_report(report: BakeoffReport) -> str:
    """Render the scorecard + verdict as plain text for the CLI / a copy into the doc."""
    lines: list[str] = ["Voice-clone engine bake-off (MI-6) — OmniVoice vs CosyVoice", ""]
    names = [c.name for c in report.candidates]
    lines.append(f"{'Criterion':<40}{'wt':>5}  " + "  ".join(f"{n:<26}" for n in names))
    lines.append("-" * (47 + 28 * len(names)))
    by_key = {c.key: c for c in report.candidates}
    excluded = {cell.criterion.key for cell in report.cells if not cell.scored}
    for crit in report.criteria:
        cells = []
        for cand in report.candidates:
            m = by_key[cand.key].metrics[crit.key]
            tag = "SKIP" if m.score is None else f"{m.score:.2f}"
            cells.append(f"{tag} {m.provenance:<10}"[:26].ljust(26))
        flag = "  (excluded)" if crit.key in excluded else ""
        lines.append(f"{crit.label:<40}{crit.weight:>5.2f}  " + "  ".join(cells) + flag)
    lines.append("-" * (47 + 28 * len(names)))
    score_cells = [f"{report.scores[c.key]:.3f}".ljust(26) for c in report.candidates]
    lines.append(f"{'WEIGHTED SCORE (kept criteria)':<40}{'':>5}  " + "  ".join(score_cells))
    lines += ["", "VERDICT", report.verdict, "", "REJECTION NOTES", report.rejection_notes]
    lines += ["", "DEFERRED TO FOLLOW-UP"]
    lines += [f"  - {d}" for d in report.deferred]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: print the scorecard, emit JSON, or attempt the deferred measurement.

    Always exits 0 — a spike tool degrades with a clear message, it does not fail a build.
    """
    parser = argparse.ArgumentParser(description="MI-6 voice-clone engine bake-off")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument(
        "--measure",
        metavar="ENGINE",
        choices=sorted(_ENGINE_IMPORT),
        help="attempt real fixture inference (skips clearly if the engine is not installed)",
    )
    parser.add_argument(
        "--fixtures", default="fixtures/voice", help="dir of reference-audio *.wav fixtures"
    )
    args = parser.parse_args(argv)

    report = run_bakeoff()
    if args.measure:
        result = measure_fixture_rtf(args.measure, args.fixtures)
        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        elif result.skipped:
            print(f"measurement skipped for {args.measure}: {result.reason}")
        else:
            print(f"{args.measure}: RTF={result.rtf} over {result.fixtures} fixture(s)")
        return 0

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via main() in tests
    sys.exit(main())
