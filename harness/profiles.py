"""Profiles — named bundles of concrete verification commands.

A *profile* is a stable name (``fast``/``web``/``replay``/``full``/``scan``) that maps to
the exact shell commands a change of that shape must pass. Specs declare
``requiredProfiles`` by name; ``explain`` prints the resolved commands; ``run`` executes
them. Keeping the name→command mapping here (one place) means a spec never hardcodes a
command line, so retargeting the whole suite (e.g. a new test runner) is a one-file edit.

Commands run from the **repo root** on the interpreter :func:`resolve_python` picks.
``{tests}`` in a template is
substituted with the caller-supplied pytest node-ids (``run --diff`` fills these from the
touched-area → required-test mapping); a profile with no ``{tests}`` slot ignores them.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


def resolve_python(repo_root: Path | None = None) -> str:
    """The interpreter every profile command runs under, as an ABSOLUTE path.

    This was the cwd-relative literal ``".venv/bin/python"``, which is only correct when
    the process happens to sit in a checkout that has a ``.venv``. A git worktree does
    not: ``harness validate``'s whole-suite collection could not even launch there
    (``[Errno 2] No such file or directory: '.venv/bin/python'``), so reference
    resolution collapsed into one "could not collect the test suite" error and three
    tests in ``tests/test_harness_validate.py`` failed in EVERY worktree — for long
    enough that sessions learned to wave them off as pre-existing (SH6.x).

    Resolution, most specific first:

    1. this tree's own ``.venv/bin/python`` when it exists — the documented dev setup,
       and the interpreter that definitely carries the dev extras;
    2. :data:`sys.executable` — whatever is running the harness. In a worktree, in CI, or
       in a container that is the honest answer: it already imported ``harness``, and the
       commands it launches run from the repo root, so pytest's ``pythonpath`` ini points
       them at THIS tree's sources rather than the venv's editable install.

    ``repo_root`` is injectable so both branches are testable; it defaults to the tree
    this module lives in (never the cwd).
    """
    root = repo_root if repo_root is not None else Path(__file__).resolve().parent.parent
    venv_py = root / ".venv" / "bin" / "python"
    if venv_py.is_file():
        return str(venv_py)
    return sys.executable


# Used as the python for every profile command so the harness never accidentally runs
# under a system interpreter missing the dev extras. Resolved once at import (the value
# cannot change under a running process).
HARNESS_PY = resolve_python()


@dataclass(frozen=True)
class Profile:
    """One profile. ``commands`` are shell strings run in order; a non-zero exit fails
    the profile. ``needs_tests`` marks profiles whose commands template ``{tests}`` — the
    CLI errors clearly if such a profile is run with no node-ids resolved."""

    name: str
    description: str
    commands: tuple[str, ...]
    needs_tests: bool = False


# ── The profile registry ────────────────────────────────────────────────────────
#
# `scan` is a MARKER profile with no shell command — the boundary scanner runs in-process
# (harness.scanner) so it can be diff-line-scoped; the CLI dispatches it directly when the
# profile is selected. Every other profile resolves to shell commands via resolve_commands.

_REGISTRY: dict[str, Profile] = {}


def _register(p: Profile) -> None:
    _REGISTRY[p.name] = p


_register(
    Profile(
        name="fast",
        description="Targeted pytest over the node-ids a change touches (the inner loop).",
        commands=(f"{HARNESS_PY} -m pytest {{tests}}",),
        needs_tests=True,
    )
)
_register(
    Profile(
        name="web",
        description="Frontend gate: typecheck + vitest (run from the repo root).",
        # Root scripts proxy into the web workspace — never `cd web` (npm/cli#4828).
        commands=("npm run typecheck:web", "npm run test:web"),
    )
)
_register(
    Profile(
        name="replay",
        description="Event-trace replay: FE-fold replay (vitest) + backend metric gate vs "
        "checked-in baselines.",
        # Two drivers, matching where the pure folds live (§2.2): the vitest replay of the
        # chat coalescer / run fold, and the Python backend-stream metric gate.
        commands=(
            "npm run test:web -- --run src/harness/replayFold.test.ts",
            f"{HARNESS_PY} -m harness replay",
        ),
    )
)
_register(
    Profile(
        name="full",
        description="The full pytest suite (the pre-final-commit gate: `make test`).",
        commands=("make test",),
    )
)
_register(
    Profile(
        name="exemplars",
        description="Per-slice WF2 milestone exemplars (§4.1): run every exemplar's ≤30s "
        "smoke script through the real engine with a fake model. Regression anchors.",
        # `python -m harness.exemplars` discovers every exemplars/slice_* bundle and runs its
        # smoke script; a non-zero exit from any one fails the profile.
        commands=(f"{HARNESS_PY} -m harness.exemplars",),
    )
)
_register(
    Profile(
        name="scan",
        description="Static architectural-boundary scanner (runs in-process, not a shell "
        "command — the CLI invokes harness.scanner when this profile is selected).",
        # `scan` is a MARKER profile: it has no shell command because the scanner runs
        # in-process (harness/scanner.py) via the CLI's _run_scan_if_selected, so it can be
        # diff-line-scoped. Selecting it (by a task spec or a forced-profile rule) triggers
        # the scan; there is nothing to resolve into resolve_commands().
        commands=(),
    )
)


def get_profile(name: str) -> Profile | None:
    """Look up a profile by name, or ``None`` if unknown."""
    return _REGISTRY.get(name)


def all_profiles() -> list[Profile]:
    """Every registered profile, in registration order."""
    return list(_REGISTRY.values())


def profile_names() -> set[str]:
    """The set of known profile names (used by ``validate`` to catch typos in
    ``requiredProfiles``)."""
    return set(_REGISTRY)


@dataclass
class ResolvedCommand:
    """A concrete command to run, with the profile it came from (for reporting)."""

    profile: str
    command: str


def resolve_commands(profiles: list[str], tests: list[str] | None = None) -> list[ResolvedCommand]:
    """Resolve a list of profile names to the ordered, de-duplicated commands to run.

    ``tests`` node-ids are joined and substituted into any ``{tests}`` slot. A profile
    with ``needs_tests`` and no node-ids is skipped with no command (the caller decides
    whether that is an error); an unknown profile name is skipped here and caught upstream
    by validation. Duplicate command strings (same profile requested twice) collapse.
    """
    joined = " ".join(tests or [])
    out: list[ResolvedCommand] = []
    seen: set[str] = set()
    for name in profiles:
        prof = _REGISTRY.get(name)
        if prof is None:
            continue
        for tmpl in prof.commands:
            if "{tests}" in tmpl:
                if not joined:
                    continue  # needs node-ids we don't have; caller reports
                cmd = tmpl.replace("{tests}", joined)
            else:
                cmd = tmpl
            if cmd not in seen:
                seen.add(cmd)
                out.append(ResolvedCommand(profile=name, command=cmd))
    return out


# Registration hook for later sessions: Session 2's scanner and Session 3's replay driver
# call this to swap their placeholder profile for the real command once implemented,
# keeping the profile *name* (and every spec that references it) stable.
def override_commands(name: str, commands: tuple[str, ...], *, needs_tests: bool = False) -> None:
    """Replace a registered profile's commands in place (same name, same description).

    Used by later-session modules to fill in a placeholder profile (``scan``/``replay``)
    without changing the name specs reference. Raises ``KeyError`` if the name is unknown
    so a typo can't silently create a new profile."""
    existing = _REGISTRY[name]
    _REGISTRY[name] = Profile(
        name=existing.name,
        description=existing.description,
        commands=commands,
        needs_tests=needs_tests,
    )
