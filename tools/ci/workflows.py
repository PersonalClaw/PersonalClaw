"""The workflows a candidate's checks run in, named in one place (#3915).

``pr.yml`` (:data:`FEEDBACK`, the change tier) runs on every pull request, and ``merge.yml``
(:data:`MERGE`, the merge tier and the push stage) on every pull request and every push to main.
They took ``ci.yml``'s triggers when it was deleted. Each runtime reader of a candidate's runs
names the workflows through this module, and
``tests/rails/test_the_candidate_workflows_are_named_in_one_place_tree.py`` fails any file that
names the deleted workflow in code.

A workflow of its own that a pull request's paths start is a gate of the merge verdict
(:data:`GATES`): the verdict reads its runs on the head, by path, event and head, and main's copy
of its filter decides when a head needs one. Every other workflow a pull request's events start
is read by the verdict itself (:data:`READ`) or gates nothing, for the reason
:data:`NOT_GATING` gives, and a row of the CI contract holds every such workflow to one of the
three.

Standard library only, like every ``tools/ci`` module.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The change tier's quick jobs, on every pull request.
FEEDBACK = "pr.yml"
#: The merge tier on every pull request, and the push stage on every push to main.
MERGE = "merge.yml"

#: Each workflow by its ``name:``, the name a ``workflow_run`` listener follows it by.
NAMES: dict[str, str] = {FEEDBACK: "PR", MERGE: "Merge"}

#: The workflows whose push to main is that commit's run.
MAIN_PUSH: tuple[str, ...] = (MERGE,)

#: What a workflow's file name stands in for where a reader takes one and means main's push runs,
#: :data:`MAIN_PUSH` in turn (``shards plan --leg test-shard:main-push:junit-test-shard-:3``).
MAIN_PUSH_NAME = "main-push"


def named(name: str) -> tuple[str, ...]:
    """The workflow files *name* stands for, in the order a reader tries them: :data:`MAIN_PUSH`
    for :data:`MAIN_PUSH_NAME`, else the one file."""
    return MAIN_PUSH if name == MAIN_PUSH_NAME else (name,)


#: The events that start a workflow for a pull request, or for a queue commit of one.
PULL_EVENTS: tuple[str, ...] = ("pull_request", "pull_request_target", "merge_group")


@dataclass(frozen=True)
class Gate:
    """A workflow of its own that a pull request's paths start, whose runs on a head gate its merge
    verdict: its file, its ``name:`` (what a ``workflow_run`` wake names it by), the jobs
    each run must pass on every attempt, the steps that fetch only outside inputs before any of the
    head's code runs (a red there, like a cancelled job, is flake-class), and what the verdict
    calls it."""

    file: str
    name: str
    jobs: tuple[str, ...]
    outside: tuple[str, ...]
    what: str

    @property
    def path(self) -> str:
        return f".github/workflows/{self.file}"


#: The gates. GitHub's own "Set up job" step fetches the job's actions, and each checkout and
#: "Install Nix" fetch only the repository and the installer.
GATES: tuple[Gate, ...] = (
    Gate("nix.yml", "Nix", ("flake",), ("Set up job", "Install Nix"), "Nix build"),
    Gate(
        "action-pins.yml", "Action pins", ("sha-matches-tag",), ("Set up job",), "action pin check"
    ),
    Gate(
        "release-image-tooling.yml",
        "Release image tooling",
        ("pin-smoke", "layout-smoke"),
        ("Set up job",),
        "release image tooling run",
    ),
)

#: A step with no name of its own that checks out the repository: GitHub calls it
#: ``Run actions/checkout@<sha>``.
OUTSIDE_PREFIXES: tuple[str, ...] = ("Run actions/checkout@",)

#: The workflows a pull request's events start that the merge verdict reads itself: the merge
#: tier's run, the sign-off check and the dependency review.
READ: tuple[str, ...] = (MERGE, "dco.yml", "dependency-review.yml")

#: Every other workflow a pull request's events start, and why the merge verdict needs no run of it.
NOT_GATING: dict[str, str] = {
    FEEDBACK: (
        "the change tier: each of its jobs is a merge-tier job that merge.yml runs again on the"
        " same head, planned by the same reasons, and the verdict judges that run"
    ),
    "cancel-closed-pr-runs.yml": "it runs only when a pull request closes, and judges no head",
}

#: A filter pattern this reads as GitHub does: path segments of word characters, ``.`` and ``-``,
#: each with any ``*`` (which stops at ``/``), and at most a last segment of ``**`` (which crosses
#: it), after an optional ``!``. GitHub reads more (``?`` and ``+`` repeat the character before
#: them, ``[...]`` is a set, and a leading ``**/`` matches no folder too), and a pattern with any
#: of it is not read: :func:`pull_request_paths` refuses it, so a verdict that reads it is red.
_SEGMENT = r"(?:[\w.-]|\*(?!\*))+"
_MODELLED = re.compile(rf"!?(?:{_SEGMENT}(?:/{_SEGMENT})*(?:/\*\*)?|\*\*)")


def filter_modelled(pattern: str) -> bool:
    """Whether :func:`filter_matches` reads *pattern* as GitHub does."""
    return bool(_MODELLED.fullmatch(pattern))


def _glob(pattern: str) -> re.Pattern[str]:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out)


def filter_matches(patterns: list[str], path: str) -> bool:
    """Whether GitHub's ``paths:`` filter *patterns* start a workflow for *path*: read in order, a
    later ``!pattern`` taking back what an earlier one matched. Raises ValueError for a pattern
    :func:`filter_modelled` refuses."""
    hit = False
    for pattern in patterns:
        if not filter_modelled(pattern):
            raise ValueError(f"names a filter pattern this does not read: {pattern!r}")
        if pattern.startswith("!"):
            hit = hit and not _glob(pattern[1:]).fullmatch(path)
        else:
            hit = hit or bool(_glob(pattern).fullmatch(path))
    return hit


def triggers(doc: object) -> Mapping[Any, Any]:
    """A workflow's ``on:``, as a mapping of event to its settings (YAML 1.1 reads a bare ``on``
    key as True; a list or a string of events is read as events with no settings)."""
    if not isinstance(doc, Mapping):
        return {}
    on = doc.get("on", doc.get(True, {}))
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {event: None for event in on if isinstance(event, str)}
    return on if isinstance(on, Mapping) else {}


def pull_request_paths(doc: object) -> list[str]:
    """A gate's ``on.pull_request.paths``, which must be all its ``pull_request`` holds: a
    non-empty list of patterns :func:`filter_modelled` reads. Raises ValueError saying what else
    it holds (``paths-ignore``, ``branches``, ``types``, an unread pattern, or no filter)."""
    spec = triggers(doc).get("pull_request")
    if not isinstance(spec, Mapping):
        raise ValueError("has no pull_request filter")
    others = sorted(str(k) for k in spec if k != "paths")
    if others:
        raise ValueError(f"filters pull_request by {', '.join(others)}, which this does not read")
    paths = spec.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        raise ValueError("has no pull_request paths list")
    unread = [p for p in paths if not filter_modelled(p)]
    if unread:
        raise ValueError(f"names a filter pattern this does not read: {unread[0]!r}")
    return list(paths)
