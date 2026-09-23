#!/usr/bin/env python3
"""Publication-hygiene check over the tracked tree — is every published path fit to publish?

This repository is PUBLIC. `git ls-files` is, exactly, the list of things the world gets,
and `git rm` removes a path from the tree but never from history. So the only cheap moment
to refuse an unfit path is BEFORE it lands, and the only thing that can refuse it on every
PR is a rail.

The rules live in ``publication-hygiene-baseline.json`` (hand-authored policy, each rule
carrying its own rationale); this module only MEASURES the tree against them. That split is
deliberate and is the one thing not to "simplify":

⚠️  THERE IS NO REGENERATE MODE, ON PURPOSE. The sibling rails in this repo
    (``config-baseline``, ``inert-surface``, ``docs-lint``, the three structural ratchets)
    are generated censuses of populations that legitimately move, so each ships a generator
    and a shrink-only ratchet. A publication denylist does not move: a match is a defect, and
    a "regenerate the baseline" verb would exist only to bless one. The escape hatch is the
    reviewed ``allowed`` list, which costs a rationale a stranger reading the public repo
    would accept.

⚠️  AND THIS RAIL SHIPS AT ZERO, which is the opposite of the structural ratchets' "ship at
    the measured population, never at zero" ruling — deliberately, because the situation is
    the inverse of the one that ruling warns about. That ruling protects against a never-run
    gate given teeth at zero reding a whole tree of pre-existing decay at once. Here the
    decay was REMOVED in the same change that added the rail (``temp-screenshots/`` deleted,
    ``scratch/`` renamed, two ``/Users/<maintainer>`` leaks scrubbed), so zero IS the measured
    population. Any nonzero result is a genuine regression, not a backlog.

Run it directly for the report::

    python3 scripts/check_publication_hygiene.py        # exit 0 iff the tree is clean
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Read cap per file for the content rule. The longest real-home path we need to see is a
#: few hundred bytes; reading whole multi-megabyte sources to find one would make the rail
#: slow enough that someone switches it off.
_CONTENT_READ_CAP = 2_000_000


def baseline_path() -> Path:
    """The committed policy file. Named to match the sibling ``*-baseline.json`` rails."""
    return REPO_ROOT / "publication-hygiene-baseline.json"


def load_baseline() -> dict:
    """Parse the committed policy. Raises rather than defaulting: a rail that silently
    falls back to an empty rule set when its policy file is unreadable is a rail that
    reports PASS for the rest of the repository's life."""
    return json.loads(baseline_path().read_text(encoding="utf-8"))


def tracked_files(root: Path | None = None) -> list[str]:
    """Every tracked path, as a repo-relative POSIX string — i.e. exactly what a clone gets.

    ``-z`` because a published repo may legitimately contain a path with a space, and
    line-splitting ``git ls-files`` would silently drop the tail of one.
    """
    root = root or REPO_ROOT
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _allowed_pairs(baseline: dict) -> set[tuple[str, str]]:
    """``(path, rule)`` pairs the policy deliberately exempts. Keyed on BOTH so an exemption
    granted for one rule cannot silently cover a different defect in the same file."""
    return {(e["path"], e["rule"]) for e in baseline.get("allowed", [])}


def path_rule_violations(paths: list[str], baseline: dict) -> list[str]:
    """Every tracked path matching a denied path pattern, minus the reviewed exemptions."""
    allowed = _allowed_pairs(baseline)
    found = []
    for rule in baseline["path_rules"]:
        pattern = re.compile(rule["pattern"])
        for path in paths:
            if pattern.search(path) and (path, rule["name"]) not in allowed:
                found.append(f"{rule['name']}: {path}")
    return sorted(found)


def _is_binary(blob: bytes) -> bool:
    """A NUL byte in the first 8 KB — the same heuristic ``git diff`` uses to decide a file
    has no textual diff. Good enough, and it is the definition the size rule wants: the
    exemption exists for files a reader reads (source, docs, lockfiles), not for images."""
    return b"\0" in blob[:8192]


def oversized_binaries(paths: list[str], baseline: dict, root: Path | None = None) -> list[str]:
    """Tracked BINARY files over the ceiling. Text is exempt — see the rule's rationale."""
    root = root or REPO_ROOT
    rule = baseline["binary_size_rule"]
    cap = rule["max_bytes"]
    found = []
    for path in paths:
        full = root / path
        # A tracked symlink whose target is absent resolves to nothing; it is not a blob
        # that weighs on a clone, so it is not this rule's business.
        if not full.is_file():
            continue
        size = full.stat().st_size
        if size <= cap:
            continue
        if _is_binary(full.read_bytes()[:8192]):
            found.append(f"{rule['name']}: {path} is {size} bytes (ceiling {cap})")
    return sorted(found)


def real_home_paths(paths: list[str], baseline: dict, root: Path | None = None) -> list[str]:
    """Absolute paths into a home directory whose owner name is not a declared placeholder.

    Reports one line per (file, name) rather than per occurrence: the fix is always "stop
    naming that person", and forty lines for one file buries the other files.
    """
    root = root or REPO_ROOT
    rule = baseline["real_home_path_rule"]
    pattern = re.compile(rule["pattern"])
    placeholders = set(rule["placeholder_home_names"])
    found = set()
    for path in paths:
        full = root / path
        if not full.is_file():
            continue
        blob = full.read_bytes()[:_CONTENT_READ_CAP]
        if _is_binary(blob):
            continue
        for name in pattern.findall(blob.decode("utf-8", "replace")):
            if name not in placeholders:
                found.add(f"{rule['name']}: {path} names home directory '{name}'")
    return sorted(found)


def violations(root: Path | None = None) -> list[str]:
    """Every publication-hygiene defect in the tracked tree, sorted and grouped by rule
    family. Empty list == the tree is fit to publish."""
    baseline = load_baseline()
    paths = tracked_files(root)
    return [
        *path_rule_violations(paths, baseline),
        *oversized_binaries(paths, baseline, root),
        *real_home_paths(paths, baseline, root),
    ]


def main() -> int:
    """Print the report; exit ``0`` iff the tracked tree carries no unfit path."""
    found = violations()
    if not found:
        print(f"publication-hygiene: PASS ({len(tracked_files())} tracked paths, 0 unfit)")
        return 0
    print(f"publication-hygiene: FAIL ({len(found)} unfit path(s))")
    for line in found:
        print(f"  - {line}")
    print("\n" + load_baseline()["how_to_fix_a_red"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
