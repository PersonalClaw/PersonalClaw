"""Per-slice runnable exemplars for the Workflows-v2 build-out (§4.1, atom SV-8).

Each landed WF2 slice has a directory here holding three files (the contract in
``README.md``): a standalone ``exemplar.py`` exercising that slice's mechanism through the
real engine with a fake model, a ``smoke.sh`` that runs it and asserts the outcome (≤30s),
and a ``RATIONALE.md`` note. Exemplars are triple-duty: regression anchors (the ``exemplars``
profile runs their smoke scripts), recorded-trace sources for the replay scenarios, and
tutorials for future coding agents.

``discover_exemplars()`` is the single enumeration both the proving test
(``tests/test_harness_exemplars.py``) and the ``exemplars`` profile read, so a new slice
directory is picked up by both the moment it lands — which is what makes the same-PR rule
(a slice merged without its exemplar is visible) mechanical rather than a matter of memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Exemplar:
    """One slice's exemplar bundle, located on disk."""

    slice: str  #: directory name, e.g. ``slice_2``
    directory: Path
    exemplar: Path  #: the standalone runnable spec
    smoke: Path  #: the ≤30s run+assert script
    rationale: Path  #: the "what this proves" note

    @property
    def module(self) -> str:
        """The dotted module path for ``python -m <module>`` — the runnable entry point."""
        return f"harness.exemplars.{self.slice}.exemplar"


def exemplars_root() -> Path:
    """The directory this package lives in."""
    return Path(__file__).resolve().parent


def _slice_dirs(base: Path) -> list[Path]:
    """Every ``slice_*`` subdirectory, sorted by name."""
    return sorted(p for p in base.iterdir() if p.is_dir() and p.name.startswith("slice_"))


def discover_exemplars(root: Path | None = None) -> list[Exemplar]:
    """Every complete exemplar bundle under this package, sorted by slice.

    A directory named ``slice_*`` counts as an exemplar only when it carries all three
    contract files. An incomplete directory is skipped here and reported by
    ``incomplete_slices`` — the two together are how a half-landed exemplar is caught
    rather than silently ignored.
    """
    base = root or exemplars_root()
    out: list[Exemplar] = []
    for directory in _slice_dirs(base):
        exemplar = directory / "exemplar.py"
        smoke = directory / "smoke.sh"
        rationale = directory / "RATIONALE.md"
        if exemplar.is_file() and smoke.is_file() and rationale.is_file():
            out.append(
                Exemplar(
                    slice=directory.name,
                    directory=directory,
                    exemplar=exemplar,
                    smoke=smoke,
                    rationale=rationale,
                )
            )
    return out


def incomplete_slices(root: Path | None = None) -> list[str]:
    """Names of ``slice_*`` directories missing one or more contract files.

    A slice merged without its full exemplar bundle shows up here — the mechanical half of
    the plan's same-PR rule ("validate flags a slice merged without its exemplar")."""
    base = root or exemplars_root()
    missing: list[str] = []
    for directory in _slice_dirs(base):
        needed = ("exemplar.py", "smoke.sh", "RATIONALE.md")
        if not all((directory / name).is_file() for name in needed):
            missing.append(directory.name)
    return missing
