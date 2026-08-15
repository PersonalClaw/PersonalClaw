"""RunPin — the identity of one eval run (EVALUATION-SUBSTRATE amendment E1).

A score without a pin is not evidence: "the template got better" is unreadable if
the scenario, the bound model, the prompt text or the config could have moved
underneath it. Every matrix/study/gate run therefore computes a pin BEFORE it runs
anything, persists it beside the run's artifacts, and carries it on the
``results.tsv`` row — :func:`personalclaw.evals.store.append_result` refuses a row
without one, so an unpinned result cannot enter the ledger at all.

The four parts the amendment names:

* ``scenario_sha256`` — canonical-JSON hash of the scenario file (see
  :mod:`personalclaw.evals.scenarios`); reformatting does not move it, editing an
  assertion does.
* ``model_fingerprint`` — per-use-case ``Provider:model`` read from
  ``active_models.json``. Rebinding a model changes the fingerprint (and therefore
  :meth:`RunPin.model_fp`) while the scenario hash stays put: that pair is exactly
  the pin-diff query "did anything change".
* ``prompt_pack_sha256`` — hash over the RESOLVED prompt pack: for every shipped
  prompt/snippet, the home's copy when it exists (the user edited it) else the
  packaged one, plus any home-only additions.
* ``config_snapshot_ref`` — hash over the relevant ``AppConfig`` subset (the evals
  section), so a knob change that moves scores is visible in the pin.

Two further parts are pinned because ES-2 made them part of a run's identity:
``fixture_home`` (the named seed the run executes over) and ``library_version``.

This module deliberately does NOT fail soft. If the pin cannot be computed, the
caller must refuse to run rather than produce an unattributable score.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.evals import scenarios as scenario_lib
from personalclaw.evals import store

logger = logging.getLogger(__name__)

PIN_FILENAME = "pin.json"

# Length of the ``model_fp`` ledger cell — a short, greppable digest of the whole
# fingerprint dict. The full per-use-case mapping lives in the run's ``pin.json``.
MODEL_FP_LEN = 12

# The prompt-pack roots, relative to the home and to the package respectively.
_PROMPT_DIRS = ("prompts", "prompt_snippets")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── the pin ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunPin:
    """The immutable identity of one eval run."""

    scenario_id: str
    scenario_sha256: str
    model_fingerprint: dict[str, str] = field(default_factory=dict)
    prompt_pack_sha256: str = ""
    config_snapshot_ref: str = ""
    fixture_home: str = scenario_lib.DEFAULT_FIXTURE_HOME
    library_version: int = scenario_lib.LIBRARY_VERSION

    def model_fp(self) -> str:
        """Short digest of the whole fingerprint dict — the ledger's ``model_fp`` cell.

        An empty fingerprint (no model bound at all) digests to ``""`` rather than
        to the hash of ``{}``, so :meth:`is_complete` can tell "unbound" from
        "bound to nothing".
        """
        if not self.model_fingerprint:
            return ""
        canonical = scenario_lib.canonical_json(dict(self.model_fingerprint))
        return _sha256_text(canonical)[:MODEL_FP_LEN]

    def is_complete(self) -> bool:
        """Are all four amendment-named parts present?

        This is the predicate the ledger enforces. A run that could not resolve its
        scenario, read its model bindings, resolve its prompts, or load its config
        has no business writing a row.
        """
        return bool(
            self.scenario_id
            and self.scenario_sha256
            and self.model_fingerprint
            and self.prompt_pack_sha256
            and self.config_snapshot_ref
        )

    def missing_parts(self) -> list[str]:
        """Which required parts are absent — the refusal message's payload."""
        return [
            name
            for name, value in (
                ("scenario_id", self.scenario_id),
                ("scenario_sha256", self.scenario_sha256),
                ("model_fingerprint", self.model_fingerprint),
                ("prompt_pack_sha256", self.prompt_pack_sha256),
                ("config_snapshot_ref", self.config_snapshot_ref),
            )
            if not value
        ]

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "scenario_sha256": self.scenario_sha256,
            "model_fingerprint": dict(self.model_fingerprint),
            "prompt_pack_sha256": self.prompt_pack_sha256,
            "config_snapshot_ref": self.config_snapshot_ref,
            "fixture_home": self.fixture_home,
            "library_version": self.library_version,
            "model_fp": self.model_fp(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RunPin":
        raw_fp = data.get("model_fingerprint") or {}
        return cls(
            scenario_id=str(data.get("scenario_id", "")),
            scenario_sha256=str(data.get("scenario_sha256", "")),
            model_fingerprint={str(k): str(v) for k, v in dict(raw_fp).items()},
            prompt_pack_sha256=str(data.get("prompt_pack_sha256", "")),
            config_snapshot_ref=str(data.get("config_snapshot_ref", "")),
            fixture_home=str(data.get("fixture_home", scenario_lib.DEFAULT_FIXTURE_HOME)),
            library_version=int(data.get("library_version", scenario_lib.LIBRARY_VERSION) or 0),
        )

    def to_row(self) -> dict:
        """The pin's contribution to a ``results.tsv`` row."""
        return {
            "model_fp": self.model_fp(),
            "scenario_id": self.scenario_id,
            "scenario_sha256": self.scenario_sha256,
            "prompt_pack_sha256": self.prompt_pack_sha256,
            "config_snapshot_ref": self.config_snapshot_ref,
            "fixture_home": self.fixture_home,
        }

    def with_model_override(self, model: str | None) -> "RunPin":
        """The pin as it applies to a cell whose model axis overrides the binding.

        ``wrap_factory_for_model`` sets ``model_override`` on EVERY provider the
        child builds, so the honest per-cell fingerprint is the override applied to
        every use case — not a single ``chat`` entry beside stale bindings.
        """
        if not model:
            return self
        overridden = {use_case: model for use_case in (self.model_fingerprint or {"chat": ""})}
        return RunPin(
            scenario_id=self.scenario_id,
            scenario_sha256=self.scenario_sha256,
            model_fingerprint=overridden,
            prompt_pack_sha256=self.prompt_pack_sha256,
            config_snapshot_ref=self.config_snapshot_ref,
            fixture_home=self.fixture_home,
            library_version=self.library_version,
        )


# ── the four parts ───────────────────────────────────────────────────────────


def model_fingerprint() -> dict[str, str]:
    """Per-use-case ``Provider:model`` from ``active_models.json``.

    The first ref in each use case's resolution chain is the ACTIVE one (later refs
    are fallbacks), so the fingerprint records heads only. A use case bound to
    nothing is omitted rather than recorded as an empty string.
    """
    from personalclaw.providers.use_cases import load_active_models

    fingerprint: dict[str, str] = {}
    for use_case, refs in (load_active_models() or {}).items():
        head = next((str(r) for r in (refs or []) if r), "")
        if head:
            fingerprint[str(use_case)] = head
    return fingerprint


def prompt_pack_manifest() -> dict[str, str]:
    """``{"<dir>/<file>": sha256}`` over the RESOLVED prompt pack.

    Resolution mirrors what the prompt providers actually read: a file present in
    the home wins over the packaged copy of the same name, and home-only files are
    included. That makes the hash move when the user edits a prompt — which is the
    whole point of pinning it.
    """
    from personalclaw.config.loader import config_dir

    pkg_root = Path(__file__).resolve().parent.parent / "config"
    home_root = config_dir()
    manifest: dict[str, str] = {}
    for sub in _PROMPT_DIRS:
        resolved: dict[str, Path] = {}
        for root in (pkg_root / sub, home_root / sub):
            if not root.is_dir():
                continue
            for path in root.iterdir():
                if path.is_file() and path.suffix == ".md":
                    resolved[path.name] = path  # home root iterated second → it wins
        for name, path in resolved.items():
            try:
                manifest[f"{sub}/{name}"] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                logger.debug("prompt %s unreadable while pinning", path, exc_info=True)
    return manifest


def prompt_pack_sha256() -> str:
    """One hash over the whole resolved prompt pack (empty pack ⇒ ``""``)."""
    manifest = prompt_pack_manifest()
    if not manifest:
        return ""
    return _sha256_text(scenario_lib.canonical_json(manifest))


def config_snapshot_ref() -> str:
    """Hash over the run-relevant ``AppConfig`` subset (the ``evals`` section).

    Read through ``AppConfig.to_dict()`` so the pin follows the config round-trip
    contract: a new ``EvalsConfig`` field is pinned the moment it round-trips, with
    no second list to keep in sync here.
    """
    from personalclaw.config.loader import AppConfig

    subset = AppConfig.load().to_dict().get("evals") or {}
    return _sha256_text(scenario_lib.canonical_json(subset))


# ── computing + persisting ───────────────────────────────────────────────────


def compute_pin(subject: str) -> RunPin:
    """Compute the pin for ``subject`` (a scenario name or path).

    Raises :class:`~personalclaw.evals.scenarios.ScenarioLibraryError` when the
    scenario or its declared fixture home cannot be resolved. Callers must let that
    propagate: refusing to run beats producing an unattributable score.
    """
    path = scenario_lib.resolve_scenario_path(subject)
    return RunPin(
        scenario_id=path.stem,
        scenario_sha256=scenario_lib.scenario_sha256(path),
        model_fingerprint=model_fingerprint(),
        prompt_pack_sha256=prompt_pack_sha256(),
        config_snapshot_ref=config_snapshot_ref(),
        fixture_home=scenario_lib.resolve_fixture_home(path),
    )


def write_pin(directory: str | Path, pin: RunPin) -> Path:
    """Persist ``pin.json`` into a run's artifact directory."""
    path = Path(directory) / PIN_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(pin.to_dict(), indent=2, sort_keys=True) + "\n")
    return path


def read_pin(directory: str | Path) -> RunPin | None:
    """Read a run's persisted pin, or ``None`` when it has none."""
    path = Path(directory) / PIN_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return RunPin.from_dict(data) if isinstance(data, dict) else None


def matrix_pin(matrix_id: str) -> RunPin | None:
    """The pin a matrix run persisted under ``matrices/<matrix_id>/``."""
    return read_pin(store.matrix_dir(matrix_id))


# ── the pin-diff query the amendment asks for ────────────────────────────────


def pin_diff(rows: list[dict] | None = None) -> list[dict]:
    """Group ledger rows by scenario hash and report the fingerprints seen.

    "Did anything change" is this query: one entry per ``scenario_sha256`` listing
    every distinct ``model_fp`` that scored it, newest ``ts`` first. A scenario
    re-run after a model rebind shows TWO fingerprints under ONE scenario hash —
    the shape that makes a score comparison legible.
    """
    ledger = store.read_results() if rows is None else rows
    grouped: dict[str, dict] = {}
    for row in ledger:
        scenario_sha = str(row.get("scenario_sha256") or "")
        if not scenario_sha:
            continue
        entry = grouped.setdefault(
            scenario_sha,
            {
                "scenario_sha256": scenario_sha,
                "scenario_id": str(row.get("scenario_id") or ""),
                "fingerprints": [],
            },
        )
        fp = str(row.get("model_fp") or "")
        if fp and fp not in entry["fingerprints"]:
            entry["fingerprints"].append(fp)
    return sorted(grouped.values(), key=lambda e: e["scenario_sha256"])
