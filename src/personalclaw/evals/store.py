"""The evals store layout + helpers (EVALUATION-SUBSTRATE §1.1).

Everything lives under ``~/.personalclaw/evals/``. Whole-file JSON artifacts
(``experiment.json``/``aggregates.json``/``trials.json`` per matrix) go through
``atomic_write``; the cross-run ``results.tsv`` ledger is append-only — a plain
``open(..., "a")`` with the parent ensured, matching how the guardrails audit
appends its jsonl.

ES-1a physically supports the ``matrices/`` subtree and the ``results.tsv`` ledger;
ES-2 adds the ``scenarios/`` subtree (the versioned Loop-1 library — see
:mod:`personalclaw.evals.scenarios`). ES-4 added ``benchmarks/`` and ES-5 adds
``studies/`` (the pre-registered A/B studies of §2 — registration, hidden ``locked/``
checks, verdict, per-run artifacts). ``trust/`` is still unowned and still NOT
created here, because a dir with no writer is dead scaffolding. The single
``StateEntry`` for ``evals`` (in ``durability/inventory.py``) claims the whole tree
for backup regardless of which subtrees exist yet.

ES-5's second append-only file is ``study_seals.tsv`` — one row per registered study,
recording the hash of its pre-registration. It sits at the evals ROOT rather than in
the study's own directory on purpose: a hash kept beside the file it pins is pinned by
nothing (see :func:`study_seals_path`).

ES-2 also makes the ledger PINNED: :func:`append_result` takes a required ``pin``
and refuses a row whose pin is absent or incomplete. That refusal is the one
chokepoint every future writer (matrix, study, gate) passes through, so "a run
without a pin cannot be written to results.tsv" holds for writers that do not exist
yet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

if TYPE_CHECKING:  # pragma: no cover - typing only; importing at runtime would cycle
    from personalclaw.evals.pinning import RunPin

# The append-only cross-run ledger's stable, ordered columns (§1.1). A new column
# must be appended, never inserted, so old rows stay parseable by position. The
# trailing five are the ES-2 pin columns; ``model_fp`` (declared by ES-1, written by
# nobody until now) is the pin's fingerprint digest rather than a sixth new column.
RESULTS_COLUMNS: tuple[str, ...] = (
    "study_id",
    "kind",
    "verdict",
    "score_old",
    "score_new",
    "k",
    "model_fp",
    "ts",
    "scenario_id",
    "scenario_sha256",
    "prompt_pack_sha256",
    "config_snapshot_ref",
    "fixture_home",
)


#: Mode for a file that must not change after it is written (a study's
#: ``registration.json`` and its pinned ``rubric.md``). Belt to the braces of
#: :func:`write_study_registration`'s explicit refusal — the refusal is the contract,
#: the mode is what a stray editor trips over first.
IMMUTABLE_MODE = 0o400

#: Mode for a ``locked/`` check file, and for the dir that holds them (§2.2).
LOCKED_MODE = 0o600
LOCKED_DIR_MODE = 0o700


class PinRequiredError(ValueError):
    """Raised when a caller tries to write a ledger row without a complete RunPin."""


class StudySealedError(RuntimeError):
    """Raised when a caller tries to re-write an already-registered study's registration.

    §2.1's "immutable once arm-1 starts" is enforced as "immutable, full stop": a study
    whose pre-registration can be rewritten is not a pre-registration, and the window
    between registration and arm-1 buys nothing a second `register_study` call could not
    abuse. Re-registering means picking a new ``study_id``.
    """


# ── path helpers ─────────────────────────────────────────────────────────────


def evals_root() -> Path:
    """``~/.personalclaw/evals/`` — created on first reference."""
    root = config_dir() / "evals"
    root.mkdir(parents=True, exist_ok=True)
    return root


def matrices_dir() -> Path:
    """``evals/matrices/`` — the experiment-matrix output tree."""
    d = evals_root() / "matrices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def matrix_dir(matrix_id: str) -> Path:
    """``evals/matrices/<matrix_id>/`` — one matrix's artifact directory."""
    d = matrices_dir() / matrix_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def scenarios_dir() -> Path:
    """``evals/scenarios/`` — the installed, versioned Loop-1 scenario library."""
    d = evals_root() / "scenarios"
    d.mkdir(parents=True, exist_ok=True)
    return d


def benchmarks_dir() -> Path:
    """``evals/benchmarks/`` — the fixture-set tree (§1.1).

    Created on reference now that ES-4 is a writer of it. The judge benchmark reads its
    shipped fixture sets out of the PACKAGE and only looks here for the user's own or
    locally edited sets (the ``prompt_pack`` resolution rule: the home wins when it has a
    file of that name), so this dir stays empty until the user puts something in it.
    """
    d = evals_root() / "benchmarks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def judge_benchmarks_dir() -> Path:
    """``evals/benchmarks/judge/`` — judge fixture sets (§6)."""
    d = benchmarks_dir() / "judge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def studies_dir() -> Path:
    """``evals/studies/`` — one directory per pre-registered study (§2, ES-5)."""
    d = evals_root() / "studies"
    d.mkdir(parents=True, exist_ok=True)
    return d


def study_dir(study_id: str) -> Path:
    """``evals/studies/<study_id>/`` — one study's artifact directory."""
    d = studies_dir() / study_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def study_locked_dir(study_id: str) -> Path:
    """``evals/studies/<study_id>/locked/`` — the hidden validation checks (§2.2).

    Created ``0700`` and its files ``0600``: §2.2 says the checks are "never rendered
    into any worker session's prompt, bindings, or workspace", and a mode is the one
    part of that promise the filesystem can keep on its own. The structural guarantee
    is :func:`personalclaw.evals.studies.assert_no_locked_leakage`; this is the floor
    under it.
    """
    d = study_dir(study_id) / "locked"
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(LOCKED_DIR_MODE)
    except OSError:  # pragma: no cover - a filesystem without modes (some CI mounts)
        pass
    return d


def results_path() -> Path:
    """``evals/results.tsv`` — the append-only cross-run ledger."""
    return evals_root() / "results.tsv"


# ── the append-only results ledger ───────────────────────────────────────────


def _tsv_cell(value: object) -> str:
    """Render one cell, neutralizing the tab/newline that would break the row."""
    text = "" if value is None else str(value)
    return text.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def append_result(row: dict, *, pin: "RunPin") -> None:
    """Append one tab-separated line to ``evals/results.tsv``.

    The ledger is append-only (every attempt logged, including failures) — so this
    is a plain ``open(..., "a")`` rather than ``atomic_write`` (which rewrites whole
    files). The file is created with a header row when absent; columns are the
    stable, ordered :data:`RESULTS_COLUMNS`, and unknown keys in ``row`` are ignored
    so a caller cannot silently widen the ledger.

    ``pin`` is REQUIRED (ES-2). A missing or incomplete
    :class:`~personalclaw.evals.pinning.RunPin` raises :class:`PinRequiredError`
    BEFORE the file is touched — an unattributable score never lands in the ledger,
    and the pin's own columns are written from the pin, not from ``row``, so a caller
    cannot pass a score with someone else's pin values.
    """
    if pin is None or not pin.is_complete():
        missing = pin.missing_parts() if pin is not None else ["<no pin>"]
        raise PinRequiredError(
            "refusing to write results.tsv without a complete RunPin "
            f"(missing: {', '.join(missing)})"
        )
    row = {**row, **pin.to_row()}
    path = results_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    need_header = not path.exists()
    line = "\t".join(_tsv_cell(row.get(col)) for col in RESULTS_COLUMNS)
    with path.open("a", encoding="utf-8") as fh:
        if need_header:
            fh.write("\t".join(RESULTS_COLUMNS) + "\n")
        fh.write(line + "\n")


#: The append-only registration-seal journal's columns (§2.1). Appended, never inserted,
#: for the same reason as :data:`RESULTS_COLUMNS`.
SEAL_COLUMNS: tuple[str, ...] = ("study_id", "registration_sha256", "sealed_ts")


def study_seals_path() -> Path:
    """``evals/study_seals.tsv`` — the append-only registration-seal journal (§2.1).

    Deliberately OUTSIDE ``studies/<id>/``. A hash kept beside the file it pins is pinned
    by nothing: the study directory's own ``rubric_sha256`` lives in ``registration.json``,
    so an editor with write access to that one directory can rewrite the rubric AND the
    hash that pins it and leave a study that looks perfectly consistent. The seal is the
    one record of what was registered that is not in the directory being verified.
    """
    return evals_root() / "study_seals.tsv"


def append_study_seal(study_id: str, registration_sha256: str, *, ts: float) -> Path:
    """Seal one study's registration hash. Append-only, first row wins on read.

    Appended rather than written per-study so a forged seal cannot *replace* the real one:
    :func:`read_study_seal` returns the FIRST row for an id, so the cheapest attack (append
    the hash of the edited registration) changes nothing, and the next cheapest (rewrite or
    truncate the journal) is a different and much louder act than editing one JSON field.
    This is tamper-EVIDENT, not tamper-proof — the same standard as the pinned rubric, one
    directory further out.
    """
    path = study_seals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    need_header = not path.exists()
    row = {
        "study_id": study_id,
        "registration_sha256": registration_sha256,
        "sealed_ts": f"{ts:.6f}",
    }
    with path.open("a", encoding="utf-8") as fh:
        if need_header:
            fh.write("\t".join(SEAL_COLUMNS) + "\n")
        fh.write("\t".join(_tsv_cell(row.get(col)) for col in SEAL_COLUMNS) + "\n")
    return path


def read_study_seal(study_id: str) -> str | None:
    """The FIRST sealed registration hash for ``study_id``, or ``None`` when unsealed.

    First-wins is the whole mechanism (see :func:`append_study_seal`). A malformed or short
    row is skipped rather than raising: an unreadable journal line must not be able to make
    a sealed study look unsealed by aborting the read.
    """
    path = study_seals_path()
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    for line in lines[1:]:  # skip header
        cells = line.split("\t")
        if len(cells) < 2:
            continue
        if cells[0] == study_id and cells[1]:
            return cells[1]
    return None


def read_results() -> list[dict]:
    """Read the ledger back as a list of column→value dicts (header skipped).

    Empty/missing ledger → ``[]``. Rows are split on tab and zipped to
    :data:`RESULTS_COLUMNS`; a short/long row is tolerated by ``zip`` truncation so
    a malformed line never aborts the read.
    """
    path = results_path()
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    for line in lines[1:]:  # skip header
        if not line:
            continue
        cells = line.split("\t")
        rows.append(dict(zip(RESULTS_COLUMNS, cells)))
    return rows


# ── per-matrix JSON artifacts (retention sinks ES-1b fills) ──────────────────


def write_matrix_experiment(matrix_id: str, spec_dict: dict) -> Path:
    """Persist a matrix's ``experiment.json`` (its spec) via ``atomic_write``."""
    path = matrix_dir(matrix_id) / "experiment.json"
    atomic_write(path, json.dumps(spec_dict, indent=2, sort_keys=True))
    return path


def read_matrix_experiment(matrix_id: str) -> dict | None:
    """Read a matrix's ``experiment.json``, or ``None`` if it doesn't exist."""
    path = matrix_dir(matrix_id) / "experiment.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_matrix_aggregates(matrix_id: str, agg_dict: dict) -> Path:
    """Persist a matrix's ``aggregates.json`` via ``atomic_write``."""
    path = matrix_dir(matrix_id) / "aggregates.json"
    atomic_write(path, json.dumps(agg_dict, indent=2, sort_keys=True))
    return path


def read_matrix_aggregates(matrix_id: str) -> dict | None:
    """Read a matrix's ``aggregates.json``, or ``None`` if it doesn't exist."""
    path = matrix_dir(matrix_id) / "aggregates.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_matrix_trials(matrix_id: str, cells: list) -> Path:
    """Persist a matrix's per-cell ``trials.json`` via ``atomic_write``.

    Each entry is one cell's coords/outcome/score/artifact_ref — the drill-down
    behind the ``aggregates.json`` summary (§1.2's "a surprising aggregate is always
    drillable to the run that produced it"). ``cells`` are :class:`CellResult`-shaped
    objects; they are serialized through their ``to_dict`` so this store stays
    dataclass-agnostic (no import of the matrix TYPES, no cycle).
    """
    path = matrix_dir(matrix_id) / "trials.json"
    rows = [c.to_dict() if hasattr(c, "to_dict") else dict(c) for c in cells]
    atomic_write(path, json.dumps(rows, indent=2, sort_keys=True))
    return path


def read_matrix_trials(matrix_id: str) -> list | None:
    """Read a matrix's ``trials.json``, or ``None`` if it doesn't exist."""
    path = matrix_dir(matrix_id) / "trials.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── per-study artifacts (§2.1 registration → §2.4 verdict) ───────────────────


def _write_immutable(path: Path, text: str) -> Path:
    """Write ``text`` once and mark the file read-only.

    ``atomic_write`` renames a fresh temp file into place, so it would happily replace a
    ``0400`` file — the mode is a tripwire, not the lock. The lock is the caller's
    existence check, which is why every caller of this helper does one first.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, text)
    try:
        path.chmod(IMMUTABLE_MODE)
    except OSError:  # pragma: no cover - a filesystem without modes
        pass
    return path


def registration_path(study_id: str) -> Path:
    """``evals/studies/<id>/registration.json`` — the immutable pre-registration."""
    return study_dir(study_id) / "registration.json"


def rubric_path(study_id: str) -> Path:
    """``evals/studies/<id>/rubric.md`` — the pinned rubric text the judge renders from."""
    return study_dir(study_id) / "rubric.md"


def write_study_registration(study_id: str, data: dict, *, rubric_text: str) -> Path:
    """Write a study's registration + its pinned rubric, ONCE.

    Raises :class:`StudySealedError` when either file already exists — including when only
    one of the two does, because a registration whose pinned rubric went missing (or the
    reverse) is a study that cannot be honestly interpreted, and silently completing the
    pair would produce a study pinned to a rubric nobody registered.
    """
    reg = registration_path(study_id)
    rub = rubric_path(study_id)
    existing = [p.name for p in (reg, rub) if p.exists()]
    if existing:
        raise StudySealedError(
            f"study {study_id!r} is already registered ({', '.join(existing)} present) — "
            "a pre-registration is immutable; register a new study_id instead"
        )
    _write_immutable(rub, rubric_text)
    return _write_immutable(reg, json.dumps(data, indent=2, sort_keys=True) + "\n")


def read_study_registration(study_id: str) -> dict | None:
    """Read a study's ``registration.json``, or ``None`` when it has none."""
    path = registration_path(study_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_study_rubric(study_id: str) -> str | None:
    """Read a study's pinned rubric text, or ``None`` when it is missing."""
    path = rubric_path(study_id)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def write_locked_check(study_id: str, name: str, data: dict) -> Path:
    """Write one ``locked/<name>.json`` check at ``0600`` (§2.2)."""
    path = study_locked_dir(study_id) / f"{name}.json"
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    try:
        path.chmod(LOCKED_MODE)
    except OSError:  # pragma: no cover - a filesystem without modes
        pass
    return path


def read_locked_checks(study_id: str) -> list[dict]:
    """Read every ``locked/*.json`` for a study, sorted by filename.

    Sorted so the check order (and therefore the artifact order) is reproducible; a
    directory-iteration order would make two runs of the same study diff for no reason.
    """
    d = study_dir(study_id) / "locked"
    if not d.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(d.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            payload.setdefault("id", path.stem)
            out.append(payload)
    return out


def write_study_verdict(study_id: str, data: dict) -> Path:
    """Persist a study's ``verdict.json`` (§2.4 — written for EVERY outcome)."""
    path = study_dir(study_id) / "verdict.json"
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    return path


def read_study_verdict(study_id: str) -> dict | None:
    """Read a study's ``verdict.json``, or ``None`` when it has not run."""
    path = study_dir(study_id) / "verdict.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_study_runs(study_id: str, rows: list) -> Path:
    """Persist a study's per-run artifacts (``runs.json``) — the §2.4 drill-down."""
    path = study_dir(study_id) / "runs.json"
    payload = [r.to_dict() if hasattr(r, "to_dict") else dict(r) for r in rows]
    atomic_write(path, json.dumps(payload, indent=2, sort_keys=True))
    return path


def read_study_runs(study_id: str) -> list | None:
    """Read a study's ``runs.json``, or ``None`` when it has none."""
    path = study_dir(study_id) / "runs.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_study_evidence(study_id: str, data: dict) -> Path:
    """Persist the evidence unit a PASSING study emits (§2.4 → §4's trust ladder)."""
    path = study_dir(study_id) / "evidence.json"
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    return path


def read_study_evidence(study_id: str) -> dict | None:
    """Read a study's evidence unit, or ``None`` — absence means "did not pass"."""
    path = study_dir(study_id) / "evidence.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def judge_calibration_dir() -> Path:
    """``evals/benchmarks/judge/calibration/`` — work the studies file for §6's harness.

    A study whose position-swap agreement fell below the floor files an item here instead
    of a template verdict (§2.3). It lives under the JUDGE benchmark tree rather than under
    the study, because the item is a request to `personalclaw judge-bench`: the thing that
    needs fixing is the judge, and a queue kept inside the study that noticed it would be a
    queue nobody drains.
    """
    d = judge_benchmarks_dir() / "calibration"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_judge_calibration_item(study_id: str, data: dict) -> Path:
    """File one judge-calibration item, keyed on the study that noticed.

    Keyed on the study id (not on a timestamp) so re-running the same study replaces its
    own item rather than piling up a duplicate per attempt — the queue should say which
    judges need work, not how many times we asked.
    """
    path = judge_calibration_dir() / f"{study_id}.json"
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    return path


def read_judge_calibration_items() -> list[dict]:
    """Every filed judge-calibration item, newest first by its recorded ``ts``."""
    d = evals_root() / "benchmarks" / "judge" / "calibration"
    if not d.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(d.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return sorted(out, key=lambda r: float(r.get("ts") or 0.0), reverse=True)


def list_study_ids() -> list[str]:
    """Every registered study id, newest-registered first.

    Ordered by the registration file's mtime rather than by name so the Learning page's
    "latest study" is the latest one, not the alphabetically luckiest id.
    """
    root = evals_root() / "studies"
    if not root.is_dir():
        return []
    pairs: list[tuple[float, str]] = []
    for d in root.iterdir():
        reg = d / "registration.json"
        if not (d.is_dir() and reg.exists()):
            continue
        try:
            pairs.append((reg.stat().st_mtime, d.name))
        except OSError:  # pragma: no cover - raced unlink
            continue
    return [name for _mtime, name in sorted(pairs, key=lambda p: (-p[0], p[1]))]
