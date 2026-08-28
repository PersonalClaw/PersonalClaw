"""The versioned Loop-1 scenario library (EVALUATION-SUBSTRATE amendment E1).

Before ES-2 the four scenarios were packaged data read straight off the install
tree (``eval/scenarios/*.json``): unversioned, unhashable as a set, and not
user-extensible. They now live in TWO places with distinct jobs:

* ``personalclaw/evals/library/*.json`` — the SHIPPED library (packaged data,
  read-only, upgraded by a release);
* ``~/.personalclaw/evals/scenarios/`` — the INSTALLED library the runner reads,
  which the user may extend with their own scenarios.

:func:`install_library` is an **idempotent backfill keyed on inspecting the data**
(not a version gate, not migration machinery): each shipped scenario is written
into the installed dir when it is absent, or when the shipped file declares a
higher ``version`` than the installed copy. A user's own scenario — or a locally
edited one whose ``version`` is >= the shipped version — is never overwritten.
The ``evals/scenario_library.json`` manifest records what is installed (per-scenario
``version``, ``sha256``, ``fixture_home``, ``origin``) so "which library produced
this row" is answerable from the home alone. ``origin`` has THREE values, not two:
:mod:`personalclaw.evals.harvest` writes cases built from real runs into the same
installed dir, and :func:`origin_of` reports those as ``harvested`` so a study can
name its population from the manifest instead of guessing at filenames. The manifest
lives BESIDE the scenarios dir, not inside it: every reader of that dir
(``personalclaw eval``, the matrix runner) globs scenario files, and a manifest sitting
among them would parse as a broken scenario.

Three scenario fields are load-bearing here:

* ``version`` (int) — drives the backfill comparison above;
* ``tiers`` (list[str]) — which evaluation tiers the scenario opted into. ``"gate"`` is
  ES-6's Loop-2 cheap subset (:mod:`personalclaw.evals.gate`); the manifest records it so
  membership is answerable from the home alone, and a shipped scenario that joins a tier
  bumps its ``version`` so the backfill actually reinstalls it;
* ``fixture_home`` (str) — the NAME of a ``tests_fixtures/`` seed the run is
  executed over, so a scenario runs from a known clean state rather than from
  whatever the user's home happens to contain. The child seeds it into a per-cell
  temp home (see :mod:`personalclaw.evals.child`); a scenario naming a fixture
  that does not ship is rejected at resolve time rather than silently run over an
  arbitrary home.

``scenario_sha256`` hashes the CANONICAL JSON of the scenario object (sorted keys,
compact separators) — reformatting a file must not change its identity, editing an
assertion must.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.evals import store

logger = logging.getLogger(__name__)

# Bumped when the SHAPE of the library/manifest changes (not when a scenario's own
# ``version`` changes — that is per-scenario and lives in the scenario file).
LIBRARY_VERSION = 1

MANIFEST_NAME = "scenario_library.json"

# Scenario file suffixes the library recognizes, in resolution order.
SCENARIO_SUFFIXES = (".json", ".yaml", ".yml")

# A scenario that declares no fixture home runs over this one. ``empty`` is the
# only fixture the wheel ships today (``personalclaw/tests_fixtures/empty``).
DEFAULT_FIXTURE_HOME = "empty"


class ScenarioLibraryError(RuntimeError):
    """A scenario could not be resolved, or declares a fixture that doesn't ship."""


# ── paths ────────────────────────────────────────────────────────────────────


def packaged_library_dir() -> Path:
    """The shipped, read-only library dir inside the installed package."""
    return Path(__file__).resolve().parent / "library"


def installed_dir() -> Path:
    """``~/.personalclaw/evals/scenarios/`` — the library the runner reads."""
    return store.scenarios_dir()


def manifest_path() -> Path:
    """``evals/scenario_library.json`` — what is installed, and from where."""
    return store.evals_root() / MANIFEST_NAME


# ── hashing ──────────────────────────────────────────────────────────────────


def canonical_json(data: object) -> str:
    """Canonical JSON form used for every hash in this module."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def scenario_sha256(path: str | Path) -> str:
    """Hash a scenario file's canonical JSON form.

    Formatting churn (indent, key order, trailing newline) does not move the hash;
    any change to a name/turn/assertion does. A file that does not parse raises —
    an unparseable scenario must never get a stable-looking identity.
    """
    return sha256_of_scenario_data(_read_scenario_data(path))


def sha256_of_scenario_data(data: dict) -> str:
    """Hash an already-parsed scenario object (see :func:`scenario_sha256`)."""
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def _read_scenario_data(path: str | Path) -> dict:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        import yaml as _yaml  # noqa: PLC0415 - optional dep, imported only for YAML

        data = _yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ScenarioLibraryError(f"scenario {p} is not a JSON/YAML object")
    return data


# ── install (idempotent backfill) ────────────────────────────────────────────


def _scenario_version(data: dict) -> int:
    try:
        return int(data.get("version", 0) or 0)
    except (TypeError, ValueError):
        return 0


def origin_of(path: Path, data: dict) -> str:
    """Where an installed scenario came from: ``shipped``, ``harvested`` or ``local``.

    ``harvested`` is derived by INSPECTING the scenario for the ``harvest`` provenance block
    (:mod:`personalclaw.evals.harvest`) rather than from a side list of names: a case whose
    provenance was stripped stops claiming to be harvested in the same edit that makes the claim
    false. ``shipped`` still wins the comparison, so a release can never be relabelled by a file
    that happens to declare the block.
    """
    if (packaged_library_dir() / path.name).exists():
        return "shipped"
    if isinstance(data.get("harvest"), dict):
        return "harvested"
    return "local"


def tiers_of(data: dict) -> list[str]:
    """The tiers a scenario declares, normalized to a list (empty when it declares none).

    ES-6's Loop-2 gate subset is opted into with ``"tiers": ["gate"]``. Read by INSPECTING the
    scenario, like :func:`origin_of` — a side list of gate scenario names would be a second
    place the truth lives, and the one that goes stale is always the list.
    """
    raw = data.get("tiers")
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, (list, tuple)):
        return [str(t) for t in raw if str(t)]
    return []


def fixture_home_of(data: dict) -> str:
    """The scenario's declared seed-fixture NAME (default :data:`DEFAULT_FIXTURE_HOME`)."""
    raw = data.get("fixture_home") or DEFAULT_FIXTURE_HOME
    return str(raw)


def install_library() -> dict:
    """Backfill the shipped library into the installed dir; return the manifest.

    Idempotent and data-keyed: a shipped scenario is written when the installed
    copy is missing or declares a LOWER ``version``. Everything else — including a
    user's own scenarios and locally edited ones at an equal-or-higher version — is
    left untouched and still recorded in the manifest (``origin: "local"``).
    """
    target = installed_dir()
    installed: dict[str, dict] = {}

    for src in sorted(packaged_library_dir().glob("*.json")):
        try:
            shipped = _read_scenario_data(src)
        except (OSError, ValueError, ScenarioLibraryError):
            logger.warning("skipping unparseable shipped scenario %s", src, exc_info=True)
            continue
        dst = target / src.name
        write = True
        if dst.exists():
            try:
                current = _read_scenario_data(dst)
                write = _scenario_version(current) < _scenario_version(shipped)
            except (OSError, ValueError, ScenarioLibraryError):
                write = True  # unreadable installed copy → restore the shipped one
        if write:
            atomic_write(dst, json.dumps(shipped, indent=2, sort_keys=True) + "\n")

    for path in sorted(target.iterdir()):
        if path.suffix not in SCENARIO_SUFFIXES:
            continue
        try:
            data = _read_scenario_data(path)
        except (OSError, ValueError, ScenarioLibraryError):
            logger.warning("skipping unparseable installed scenario %s", path, exc_info=True)
            continue
        installed[path.stem] = {
            "version": _scenario_version(data),
            "sha256": sha256_of_scenario_data(data),
            "fixture_home": fixture_home_of(data),
            "origin": origin_of(path, data),
            # ES-6: which tiers the scenario opted into (``["gate"]`` for the Loop-2 cheap
            # subset). Recorded here so "what is in the gate subset" is answerable from the
            # manifest, the same way ``origin``/``fixture_home`` already are.
            "tiers": tiers_of(data),
        }

    manifest = {"library_version": LIBRARY_VERSION, "scenarios": installed}
    atomic_write(manifest_path(), json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def read_manifest() -> dict | None:
    """The installed library's manifest, or ``None`` when nothing is installed yet."""
    path = manifest_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_installed() -> list[str]:
    """Installed scenario names (the library is installed first if it is absent)."""
    manifest = read_manifest() or install_library()
    return sorted((manifest.get("scenarios") or {}).keys())


# ── resolution ───────────────────────────────────────────────────────────────


def resolve_scenario_path(subject: str) -> Path:
    """Resolve a matrix ``subject`` to a scenario FILE in the installed library.

    A path to an existing file wins (an ad-hoc scenario outside the library is
    still runnable and still pinnable). Otherwise ``subject`` is a bare name in
    ``~/.personalclaw/evals/scenarios/``; the library is installed on demand so a
    fresh home resolves the shipped set without a setup step.
    """
    as_path = Path(subject)
    if as_path.is_file():
        return as_path
    d = installed_dir()
    if not manifest_path().exists():
        install_library()
    for ext in SCENARIO_SUFFIXES:
        candidate = d / f"{subject}{ext}"
        if candidate.is_file():
            return candidate
    raise ScenarioLibraryError(
        f"scenario {subject!r} not found (as a path or in {d}); "
        f"installed: {', '.join(list_installed()) or '<none>'}"
    )


def resolve_fixture_home(path: str | Path) -> str:
    """The fixture-home name a scenario runs over, validated against what ships.

    An unknown fixture name is an error here, in the PARENT, before a child is
    spawned: a run over "whatever home happened to be there" is exactly the
    unpinned result this atom exists to make impossible.
    """
    name = fixture_home_of(_read_scenario_data(path))
    from personalclaw.seed import SeedError, _resolve_fixture  # noqa: PLC0415 - cycle-free

    try:
        _resolve_fixture(name)
    except SeedError as exc:
        raise ScenarioLibraryError(
            f"scenario {Path(path).name} declares fixture_home {name!r}, which does not ship: {exc}"
        ) from exc
    return name
