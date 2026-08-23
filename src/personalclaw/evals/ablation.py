"""The harness-ablation runner (EVALUATION-SUBSTRATE §3.1).

Harness components compensate for model weaknesses whose assumptions expire silently.
Nothing else in PClaw can answer "does this judge / hint / stage still pay for itself?".
This module answers it on a cadence, for ONE component per run, and files the answer as a
proposal — it never edits anything.

The shape:

1. :func:`pick_component` takes ONE row from the registry, round-robin (§3.1 step 1's
   one-at-a-time rule, applied to measurement as well as to removal).
2. :func:`run_ablation` replays that component's benchmark through the ES-1b matrix runner
   with an ``arm_mask`` axis — ``on`` / ``off`` (/ ``cheap``). The toggle is a
   :class:`~personalclaw.evals.overlay.ComponentOverlay` applied ONLY inside the spawned
   child (:func:`personalclaw.evals.overlay.apply_in_child`).
3. :func:`classify` turns the per-arm means into ``keep`` / ``remove`` / ``lighten``.
4. A ``remove`` verdict's report is attached to a LEARN-R9 ``retirement`` proposal as the
   ablation-grade evidence R9 requires (:func:`file_retirement_proposal`).

**The load-bearing negative: the live spec/config is never mutated.** Editing the real
config to toggle a component off and editing it back is the obvious implementation and it
is forbidden — a crash between the two edits leaves the operator's configuration silently
altered. So every run executes inside :func:`live_state_unchanged`, which digests the live
files before and after and raises :class:`LiveStateMutatedError` on any drift **including
after a run that raised** (the drift replaces the original error, with it chained as the
cause: a run that both failed and altered live state is a config incident first).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.evals import overlay as overlay_lib
from personalclaw.evals import store
from personalclaw.evals.matrix import MatrixSpec, aggregate_by

logger = logging.getLogger(__name__)

# ── the three-way verdict (§3.1 step 3) ──────────────────────────────────────
#: The component earns its keep: switching it off measurably degrades the benchmark.
KEEP = "keep"
#: No delta. This is the verdict that files a LEARN-R9 retirement proposal.
REMOVE = "remove"
#: A delta exists, but a deliberately cheaper variant matches the full one.
LIGHTEN = "lighten"
VERDICTS: tuple[str, ...] = (KEEP, REMOVE, LIGHTEN)
#: NOT a recommendation: an arm produced no scored cell, so there is no delta to read.
#: Reported instead of guessed — the three-state contract (§1.2) says an absent verifier is
#: never a zero, and it must not become a `remove` either.
INCONCLUSIVE = "inconclusive"

#: How large a mean-score gap counts as a real delta. Below this the arms are "the same".
DEFAULT_EPSILON = 0.02

#: The proposals-queue evidence tier a §3.1 report carries. Distinct from ``"correlated"``
#: on purpose: R9 asks for **ablation-grade** evidence, and a paired on/off measurement is
#: a different claim from a co-occurrence. Nothing else in the queue writes this tier.
ABLATION_EVIDENCE_STRENGTH = "ablation"

#: Absent-file sentinel for the live-state digest. A file that did not exist and now does is
#: a mutation, so absence has to be a recorded value rather than a skipped key.
ABSENT = "<absent>"


class LiveStateMutatedError(RuntimeError):
    """An ablation run altered the live spec/config. §3.1 forbids this outright."""


# ── the registry (§3.1 step 1) ───────────────────────────────────────────────


@dataclass(frozen=True)
class AblationComponent:
    """One ablatable harness component: what to toggle, and what to replay it over."""

    component_id: str
    kind: str  # one of overlay_lib.KINDS
    target: str  # skill name | surfacing heuristic | dotted config path
    subject: str  # the scenario the matrix runner replays
    off_value: object = False
    cheap_value: object = None
    #: Extra live files (relative to the config dir) this component's spec lives in, added
    #: to the byte-identity guard. The config/model stores are always watched.
    live_refs: list[str] = field(default_factory=list)
    description: str = ""

    def overlay(self) -> overlay_lib.ComponentOverlay:
        """The ``on``-baseline overlay; the runner rebinds it per arm."""
        return overlay_lib.ComponentOverlay(
            component_id=self.component_id,
            kind=self.kind,
            target=self.target,
            arm=overlay_lib.ARM_ON,
            off_value=self.off_value,
            cheap_value=self.cheap_value,
            notes={"subject": self.subject},
        )

    def arms(self) -> list[str]:
        """The arms this component can actually run.

        ``cheap`` appears ONLY when a cheap form is declared. A ``cheap`` arm that toggled
        nothing would score identically to ``on`` and be reported as ``lighten`` — a
        fabricated recommendation, which is why the arm is omitted rather than defaulted.
        """
        arms = [overlay_lib.ARM_ON, overlay_lib.ARM_OFF]
        if self.cheap_value is not None:
            arms.append(overlay_lib.ARM_CHEAP)
        return arms

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AblationComponent":
        return cls(
            component_id=str(data.get("component_id", "")),
            kind=str(data.get("kind", "")),
            target=str(data.get("target", "")),
            subject=str(data.get("subject", "")),
            off_value=data.get("off_value", False),
            cheap_value=data.get("cheap_value"),
            live_refs=[str(r) for r in (data.get("live_refs") or [])],
            description=str(data.get("description", "")),
        )


def registry_path() -> Path:
    """``evals/ablation_registry.json`` — the operator's registry rows."""
    return store.evals_root() / "ablation_registry.json"


def registry() -> list[AblationComponent]:
    """The ablation registry, sorted by id so the round-robin cursor is stable.

    Ships EMPTY on purpose: which components are worth measuring is a property of one
    user's harness (their templates, their skills, their hints), and a shipped list would
    spend a monthly cadence measuring components they never installed. Rows are added by
    writing :func:`registry_path`; an unparseable file yields ``[]`` and the runner reports
    "nothing registered" rather than inventing a subject.
    """
    path = registry_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("unparseable ablation registry at %s", path)
        return []
    rows = data.get("components") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    out: list[AblationComponent] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        try:
            comp = AblationComponent.from_dict(raw)
            # Validated on the way in: an unknown kind or an empty target would ablate
            # nothing and report a 0.0 delta, indistinguishable from a dead component.
            comp.overlay()
        except ValueError as exc:
            logger.warning("skipping invalid ablation registry row: %s", exc)
            continue
        if not (comp.component_id and comp.subject):
            logger.warning("skipping ablation registry row with no id/subject")
            continue
        out.append(comp)
    return sorted(out, key=lambda c: c.component_id)


# ── cadence state ────────────────────────────────────────────────────────────


def state_path() -> Path:
    """``evals/ablation_state.json`` — the cadence cursor and the run history."""
    return store.evals_root() / "ablation_state.json"


def load_state() -> dict:
    path = state_path()
    if not path.is_file():
        return {"cursor": 0, "last_run_ts": "", "history": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"cursor": 0, "last_run_ts": "", "history": []}
    if not isinstance(data, dict):
        return {"cursor": 0, "last_run_ts": "", "history": []}
    data.setdefault("cursor", 0)
    data.setdefault("last_run_ts", "")
    data.setdefault("history", [])
    return data


def save_state(state: dict) -> None:
    atomic_write(state_path(), json.dumps(state, indent=2, sort_keys=True) + "\n")


def _parse_ts(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def due(*, now: datetime | None = None, cadence_days: int | None = None) -> bool:
    """Has ``cadence_days`` elapsed since the last ablation run?

    Never run ⇒ due. ``cadence_days`` defaults to ``evals.ablation_cadence_days`` (30).
    """
    if cadence_days is None:
        cadence_days = _cadence_days()
    last = _parse_ts(load_state().get("last_run_ts") or "")
    if last is None:
        return True
    moment = now or datetime.now(tz=timezone.utc)
    return (moment - last).total_seconds() >= max(1, int(cadence_days)) * 86400.0


def pick_component(components: list[AblationComponent] | None = None) -> AblationComponent | None:
    """The ONE component this cadence measures — round-robin over the registry.

    Round-robin rather than "worst first": a priority order would starve the components
    nothing has measured yet, and the whole point is that every registered component
    eventually gets a delta. Advancing the cursor is :func:`run_cadence`'s job (a pick that
    never ran must not consume a slot).
    """
    rows = registry() if components is None else list(components)
    if not rows:
        return None
    cursor = int(load_state().get("cursor") or 0)
    return rows[cursor % len(rows)]


# ── the live-state byte-identity guard (the §3.1 constraint) ─────────────────


def _digest_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, IsADirectoryError):
        return ABSENT


def live_state_digest(extra_refs: list[str] | None = None) -> dict[str, str]:
    """sha256 of every live spec/config file an ablation could plausibly toggle.

    Always: ``config.json`` and ``active_models.json`` (the two stores the overlay kinds
    reach) plus every ``use_case_settings/*.json``. ``extra_refs`` adds a component's own
    declared spec files. Keys are config-dir-relative so the map is comparable across
    calls; a missing file digests to :data:`ABSENT`, so a file the run CREATED is drift too.
    """
    from personalclaw.config.loader import config_dir

    root = config_dir()
    digests: dict[str, str] = {}
    for rel in ("config.json", "active_models.json"):
        digests[rel] = _digest_file(root / rel)
    ucs = root / "use_case_settings"
    if ucs.is_dir():
        for path in sorted(ucs.glob("*.json")):
            digests[f"use_case_settings/{path.name}"] = _digest_file(path)
    for rel in extra_refs or []:
        key = str(rel).lstrip("/")
        digests.setdefault(key, _digest_file(root / key))
    return digests


def _normalize_config_before_snapshot() -> None:
    """Force ``config.json``'s one-time normalization BEFORE the digest is taken.

    Found by driving the real CLI against a dev home: ``AppConfig.load()`` rewrites
    ``config.json`` in full (every default filled in, plus a ``meta.lastTouchedAt`` wall-clock
    stamp) the FIRST time it loads an un-normalized file, and is a byte no-op on every load
    after that. The pin (``config_snapshot_ref``) loads config INSIDE the guarded block, so
    without this the very first ablation in a fresh or hand-edited home raised
    ``LiveStateMutatedError`` — accusing the runner of the one thing §3.1 forbids, over a
    rewrite the config loader did on its own.

    Normalizing first makes byte-identity a true statement about what the ABLATION did.
    Best-effort: an unreadable config is not a reason to refuse to measure, and it will fail
    louder a moment later inside the pin.
    """
    try:
        from personalclaw.config.loader import AppConfig

        AppConfig.load()
    except Exception:
        logger.debug("pre-snapshot config normalization skipped", exc_info=True)


def _drift(before: dict[str, str], after: dict[str, str]) -> list[str]:
    keys = sorted(set(before) | set(after))
    return [k for k in keys if before.get(k, ABSENT) != after.get(k, ABSENT)]


@contextmanager
def live_state_unchanged(extra_refs: list[str] | None = None) -> Iterator[dict[str, str]]:
    """Assert the live spec/config is byte-identical across the block.

    Raises :class:`LiveStateMutatedError` naming every drifted file. The check runs in a
    ``finally``, so **a body that raises is still checked** — that is the case the
    constraint exists for (a crash mid-run is exactly when an edit-and-edit-back
    implementation strands a mutation). When the body raised AND drifted, the drift is
    raised with the body's exception chained as ``__cause__``: a run that altered the
    operator's config is the more serious of the two facts, and neither is lost.
    """
    _normalize_config_before_snapshot()
    before = live_state_digest(extra_refs)
    failure: BaseException | None = None
    try:
        yield before
    except BaseException as exc:  # noqa: BLE001 - re-raised below; captured for chaining
        failure = exc
        raise
    finally:
        drift = _drift(before, live_state_digest(extra_refs))
        if drift:
            raise LiveStateMutatedError(
                "ablation run mutated live spec/config (forbidden by §3.1): " + ", ".join(drift)
            ) from failure


# ── verdict + report ─────────────────────────────────────────────────────────


def classify(
    on: float | None,
    off: float | None,
    cheap: float | None = None,
    *,
    epsilon: float = DEFAULT_EPSILON,
) -> str:
    """The three-way verdict from the per-arm means.

    * ``on`` or ``off`` unmeasured ⇒ :data:`INCONCLUSIVE` (no delta exists to read).
    * ``on - off < epsilon`` ⇒ :data:`REMOVE`. Note the SIGNED comparison: a component
      whose absence *improved* the benchmark is not "keep with a negative delta", it is a
      component that does not pay for itself.
    * else a declared cheap arm within ``epsilon`` of ``on`` ⇒ :data:`LIGHTEN`.
    * else :data:`KEEP`.
    """
    if on is None or off is None:
        return INCONCLUSIVE
    gain = float(on) - float(off)
    if gain < float(epsilon):
        return REMOVE
    if cheap is not None and (float(on) - float(cheap)) < float(epsilon):
        return LIGHTEN
    return KEEP


@dataclass
class AblationReport:
    """One component's keep/remove/lighten report — the §3.1 deliverable."""

    component_id: str
    kind: str
    target: str
    subject: str
    verdict: str
    arms: dict[str, dict] = field(default_factory=dict)  # arm → aggregate()
    delta: float | None = None  # on − off, None when either arm is unmeasured
    cheap_delta: float | None = None  # on − cheap, None when no cheap arm ran
    epsilon: float = DEFAULT_EPSILON
    matrix_id: str = ""
    trials: int = 0
    created_at: str = ""
    #: The live files the guard watched, with their (unchanged) digests. The report carries
    #: its own proof of non-mutation, so "did this run touch my config" is answerable from
    #: the artifact rather than from trust.
    live_state: dict[str, str] = field(default_factory=dict)

    def arm_mean(self, arm: str) -> float | None:
        agg = self.arms.get(arm) or {}
        value = agg.get("mean_score")
        return None if value is None else float(value)

    def to_dict(self) -> dict:
        return asdict(self)

    def evidence_ref(self) -> str:
        """The stable ref a proposal cites: ``ablation:<matrix_id>``."""
        return f"ablation:{self.matrix_id or self.component_id}"


def reports_dir() -> Path:
    """``evals/ablation/`` — one JSON report per run."""
    d = store.evals_root() / "ablation"
    d.mkdir(parents=True, exist_ok=True)
    return d


def report_path(matrix_id: str) -> Path:
    return reports_dir() / f"{matrix_id}.json"


def write_report(report: AblationReport) -> Path:
    path = report_path(report.matrix_id or report.component_id)
    atomic_write(path, json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
    return path


def latest_report() -> AblationReport | None:
    """The newest report on disk, or ``None``.

    Newest by the report's own ``created_at`` rather than by mtime: a report copied or
    restored from a snapshot would sort by the copy's timestamp, which is not when the
    measurement happened.
    """
    reports: list[AblationReport] = []
    for path in reports_dir().glob("*.json"):
        report = read_report(path.stem)
        if report is not None:
            reports.append(report)
    if not reports:
        return None
    return max(reports, key=lambda r: r.created_at)


def latest_ablation_view() -> dict | None:
    """The read-only payload ``GET /api/evals/ablation`` publishes.

    Everything arrives DECIDED — the verdict, the deltas, the threshold they were compared
    against. A frontend that re-derived "is this a real delta" would eventually disagree with
    the runner, and the copy shipping the permissive answer would be the UI.
    """
    report = latest_report()
    if report is None:
        return None
    state = load_state()
    return {
        "report": report.to_dict(),
        "verdict_vocabulary": list(VERDICTS),
        "registry": [c.to_dict() for c in registry()],
        "history": list(state.get("history") or [])[-20:],
        "last_run_ts": state.get("last_run_ts") or "",
        "cadence_days": _cadence_days(),
        "due": due(),
    }


def _cadence_days() -> int:
    try:
        from personalclaw.config.loader import AppConfig

        return int(AppConfig.load().evals.ablation_cadence_days)
    except Exception:
        return 30


def read_report(matrix_id: str) -> AblationReport | None:
    path = report_path(matrix_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return AblationReport(**{k: v for k, v in data.items() if k in AblationReport.__annotations__})


# ── the run ──────────────────────────────────────────────────────────────────


def validate_component(component: AblationComponent) -> None:
    """Refuse a component whose toggle would silently do nothing. Raises ``ValueError``.

    Checked in the PARENT, before a single cell is spawned: a target that ablates nothing
    scores identically on both arms and reports a fabricated no-delta ``remove``, and finding
    that out after paying for the matrix is finding out too late. The child re-checks at
    apply time (:func:`personalclaw.evals.overlay.apply_in_child`) so a hand-built overlay
    cannot bypass it either.
    """
    component.overlay()  # validates kind + arm + non-empty target
    if component.kind == overlay_lib.KIND_CONFIG_FLAG and not overlay_lib.config_field_exists(
        component.target
    ):
        raise ValueError(
            f"component {component.component_id!r} targets config field "
            f"{component.target!r}, which does not exist on AppConfig"
        )
    if component.kind == overlay_lib.KIND_SURFACING:
        from personalclaw.learning.surfacing import ABLATABLE

        if component.target not in ABLATABLE:
            raise ValueError(
                f"component {component.component_id!r} targets unknown surfacing heuristic "
                f"{component.target!r} — expected one of {ABLATABLE}"
            )


def _matrix_id(component_id: str, now: datetime) -> str:
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    slug = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in component_id)[:48]
    return f"ablation-{slug}-{stamp}"


def build_spec(
    component: AblationComponent,
    *,
    trials: int = 3,
    budget_usd: float = 0.0,
) -> MatrixSpec:
    """The matrix spec for one component: its arms on the ``arm_mask`` axis."""
    return MatrixSpec(
        subject=component.subject,
        axes={overlay_lib.ARM_AXIS: component.arms()},
        trial_count=max(1, int(trials)),
        scorer="assertion",
        budget_usd=float(budget_usd),
        component=component.overlay().to_dict(),
    )


def run_ablation(
    component: AblationComponent,
    *,
    trials: int = 3,
    budget_usd: float = 0.0,
    epsilon: float = DEFAULT_EPSILON,
    now: datetime | None = None,
    run_matrix=None,
) -> AblationReport:
    """Measure ONE component on-vs-off and return its report.

    Wrapped in :func:`live_state_unchanged` for the component's own spec files as well as
    the always-watched config stores: if the matrix run leaks a mutation into live state,
    this raises :class:`LiveStateMutatedError` instead of returning a report, whether or
    not the run itself succeeded.
    """
    if run_matrix is None:  # pragma: no cover - the default wiring
        from personalclaw.evals.runner import run_matrix as _default

        run_matrix = _default
    validate_component(component)
    moment = now or datetime.now(tz=timezone.utc)
    matrix_id = _matrix_id(component.component_id, moment)
    spec = build_spec(component, trials=trials, budget_usd=budget_usd)

    with live_state_unchanged(component.live_refs) as watched:
        result = run_matrix(spec, matrix_id=matrix_id)

    arms = aggregate_by(list(result.cells), overlay_lib.ARM_AXIS)
    on = (arms.get(overlay_lib.ARM_ON) or {}).get("mean_score")
    off = (arms.get(overlay_lib.ARM_OFF) or {}).get("mean_score")
    cheap = (arms.get(overlay_lib.ARM_CHEAP) or {}).get("mean_score")
    verdict = classify(on, off, cheap, epsilon=epsilon)
    report = AblationReport(
        component_id=component.component_id,
        kind=component.kind,
        target=component.target,
        subject=component.subject,
        verdict=verdict,
        arms=arms,
        delta=(None if (on is None or off is None) else round(float(on) - float(off), 6)),
        cheap_delta=(None if (on is None or cheap is None) else round(float(on) - float(cheap), 6)),
        epsilon=float(epsilon),
        matrix_id=matrix_id,
        trials=max(1, int(trials)),
        created_at=moment.isoformat(),
        live_state=dict(watched),
    )
    write_report(report)
    return report


# ── the LEARN-R9 attachment (the call site, not just the mechanism) ──────────


def _proposal_body(report: AblationReport) -> str:
    on = report.arm_mean(overlay_lib.ARM_ON)
    off = report.arm_mean(overlay_lib.ARM_OFF)
    return (
        f"Ablation measured `{report.component_id}` ({report.kind} → {report.target}) over "
        f"`{report.subject}` and found no delta: the benchmark scored "
        f"{'n/a' if on is None else f'{on:.3f}'} with the component ON and "
        f"{'n/a' if off is None else f'{off:.3f}'} with it OFF "
        f"(delta {report.delta}, below the {report.epsilon} threshold) across "
        f"{report.trials} paired trials per arm.\n\n"
        "A component that changes nothing is dead weight in every prompt it touches. The "
        f"full per-arm report is `evals/ablation/{report.matrix_id}.json`; the raw cells are "
        f"under `evals/matrices/{report.matrix_id}/`.\n\n"
        "The removal is yours to make — this runner never edits anything."
    )


def file_retirement_proposal(report: AblationReport):
    """Attach a no-delta report to a LEARN-R9 ``retirement`` proposal. Returns
    ``(verdict, proposal)``; ``(SKIP, None)`` when the report is not a ``remove``.

    This is the §3.1 step-3 call site: R9 requires *ablation-grade* evidence for a
    retirement and nothing previously generated any. The proposal carries
    :data:`ABLATION_EVIDENCE_STRENGTH` — a distinct tier from the queue's default
    ``"correlated"``, because a paired on/off measurement is a different claim from a
    co-occurrence, and R9's gate is on the strength, not on the presence, of evidence.
    """
    from personalclaw.learning import proposals

    if report.verdict != REMOVE:
        return proposals.Verdict.SKIP, None
    return proposals.enqueue(
        kind=proposals.Kind.RETIREMENT.value,
        title=f"Retire {report.component_id} — ablation measured no delta",
        body=_proposal_body(report),
        # One target per component, so a re-measured component reinforces its own row rather
        # than filing a second one every cadence.
        target=f"ablation.{report.component_id}",
        provenance="inferred",
        source_cadence="ablation",
        evidence_refs=[report.evidence_ref(), f"matrix:{report.matrix_id}"],
        evidence_strength=ABLATION_EVIDENCE_STRENGTH,
        confidence=0.7,
        tags=["ablation", report.kind, REMOVE],
        # Each paired trial is an independent observation of the null result, so the queue's
        # default evidence floor is left ALONE rather than lowered: a sweep that had to lower
        # the floor to be heard would be one firing on too little measurement.
        occurrences=report.trials,
    )


#: A matrix run outlives a maintenance tick, so two ticks could otherwise start the same
#: component twice (``last_run_ts`` is stamped AFTER the run, on purpose — a failed run must
#: stay due). Non-blocking, so the second tick reports "already running" instead of queueing.
_CADENCE_LOCK = threading.Lock()


def run_cadence(
    *,
    now: datetime | None = None,
    cadence_days: int | None = None,
    trials: int = 3,
    force: bool = False,
    run_matrix=None,
) -> dict:
    """The periodic entry point: one component, measured, reported, and proposed.

    Returns a summary dict — ``{"ran": bool, "reason": str, ...}``. Never raises for "not
    due" or "nothing registered"; those are ordinary outcomes of a cadence tick.
    """
    if not _CADENCE_LOCK.acquire(blocking=False):
        return {"ran": False, "reason": "already_running"}
    try:
        return _run_cadence(
            now=now, cadence_days=cadence_days, trials=trials, force=force, run_matrix=run_matrix
        )
    finally:
        _CADENCE_LOCK.release()


def _run_cadence(
    *,
    now: datetime | None = None,
    cadence_days: int | None = None,
    trials: int = 3,
    force: bool = False,
    run_matrix=None,
) -> dict:
    moment = now or datetime.now(tz=timezone.utc)
    if not force and not due(now=moment, cadence_days=cadence_days):
        return {"ran": False, "reason": "not_due"}
    rows = registry()
    if not rows:
        return {"ran": False, "reason": "empty_registry"}
    component = pick_component(rows)
    assert component is not None  # non-empty registry
    report = run_ablation(component, trials=trials, now=moment, run_matrix=run_matrix)
    filed = ""
    if report.verdict == REMOVE:
        verdict, proposal = file_retirement_proposal(report)
        filed = proposal.id if proposal is not None else f"not_filed:{verdict.value}"

    state = load_state()
    # The cursor advances only after a component actually ran, so a cadence that found an
    # empty registry does not silently skip a component's turn.
    state["cursor"] = (int(state.get("cursor") or 0) + 1) % len(rows)
    state["last_run_ts"] = moment.isoformat()
    history = list(state.get("history") or [])
    history.append(
        {
            "ts": moment.isoformat(),
            "component_id": component.component_id,
            "verdict": report.verdict,
            "matrix_id": report.matrix_id,
            "delta": report.delta,
            "proposal": filed,
        }
    )
    state["history"] = history[-100:]
    save_state(state)
    return {
        "ran": True,
        "reason": "ok",
        "component_id": component.component_id,
        "verdict": report.verdict,
        "matrix_id": report.matrix_id,
        "delta": report.delta,
        "proposal": filed,
    }
