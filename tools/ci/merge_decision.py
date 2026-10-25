"""The merge controller's decision: green, red or pending, with its reason and its receipt.

:func:`decide` reads five things the controller gathered, plus the controller's own revision
for the receipt, and a full clone (``git_dir``) holding ``main``, where it checks the base:

- ``plan``: the planned job keys. Until the planner lands, :func:`planned_jobs` reads them from
  the default branch's catalogue, at ``main``'s tip, and never from the candidate's own files or
  a worktree, so a candidate that drops a job from its workflow or its catalogue cannot shrink
  what its verdict covers.
- ``job_results``: the candidate's own run (:class:`RunResults`), or None before it reports.
- ``comparison``: ``trust.compare`` of the default branch's commit (``base``, ``main``'s tip)
  and the head.
- ``identity``: the candidate as it stands now (:class:`Candidate`): its head, the head's tree and
  each partner repository's current head.
- ``trusted_run``: a run of the default branch's definitions on the candidate's tree, or None.

The rules, in order:

1. A comparison of another head is red: it says nothing about this one. So is a comparison whose
   ``base`` is not ``main``'s tip in ``git_dir``: a full commit id (``trust.require_on_main``)
   that is ``main`` itself. A base off main must never pick which run counts, and nor may an
   older commit on main: against it, a candidate that puts back a definition main has changed
   since shows no change, and its own run would count.
2. When no definition changed, the candidate's own run counts. When any did, that run is advisory
   and only ``trusted_run`` counts, so a job kept by name but changed to always pass, an edited
   ``tools/ci/verdict.py`` or an emptied ``Makefile`` target is judged by the default branch's
   copy.
3. No counted run yet, a counted run of an older head, a run of other definitions than the ones
   rule 2 chose, or a run against other partner heads than the current ones is pending. So a
   partner head that moves takes a green back to pending until a run on the new pair reports.
4. A counted run naming this head with another tree is red: a head has one tree.
5. The run's ``toJSON(needs)`` is read with ``verdict.from_needs`` and judged with
   ``verdict.decide``: a planned job that is missing, cancelled, skipped or red is red, even when
   the candidate's own workflow dropped it. So is :data:`CATALOGUE_CHECK`, the job whose step
   runs main's ``tools/ci`` over the candidate's catalogue: no plan names it, and it is required
   beside every non-empty plan, so a refused check reds the decision, and a missing or skipped
   one is never read as no objection. Results that cannot be read are red, and so is a plan
   that cannot be, named as the plan's fault.
6. A green is written as a receipt in ``trust``'s format with ``merge_verdict.render_output``. A
   receipt that is not complete (no toolchain, no evidence) is red, never a green without one.
   A red or pending output carries no receipt, so ``verify-check-run`` can never accept it.

This is a pull request's reading: it judges with ``verdict.decide``, never
``verdict.decide_main_push``, so a cancelled planned job is red as cancelled and never read as
superseded.

``toJSON(needs)`` holds each job's latest attempt only. Reading a red from an earlier attempt of
the same run, from the API, is the controller workflow's job, not this module's: what it reads
goes in as a further :class:`RunResults` judgement, not through ``needs``.

Nothing here posts, fetches or runs a job; the controller workflow comes later. It reads git in
``git_dir`` only. Standard library only, and nothing in a tree it judges is imported or run. See
``docs/maintainers/ci.md`` ("The merge controller's decision").
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import catalogue, merge_verdict, trust, verdict

from_needs = verdict.from_needs

GREEN = "green"
RED = "red"
PENDING = "pending"

#: Which run a decision counted.
CANDIDATE_RUN = "candidate"
DEFAULT_BRANCH_RUN = "default branch"

#: The ``merge.yml`` job whose ``catalogue-check`` step runs main's ``tools/ci`` over the
#: candidate's catalogue (``tools/ci/catalogue_check.py``). No catalogue entry, so no plan, names
#: it; rule 5 requires it of the run that counts, beside the plan.
CATALOGUE_CHECK = "changes"

#: A full object id, SHA-1 or SHA-256. Matched with ``fullmatch``, so nothing may follow it.
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def _sha(name: str, value: object) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise trust.Refused(f"{name} must be a full object id, got {value!r}")
    return value


def _partners(value: Mapping[str, str]) -> dict[str, str]:
    out = dict(value)
    for name, sha in out.items():
        if not isinstance(name, str) or not name:
            raise trust.Refused("a partner revision must be named")
        _sha(f"partner {name}", sha)
    return out


@dataclass(frozen=True)
class Candidate:
    """The candidate as it stands now. ``external_revisions`` are the partners' current heads."""

    head_sha: str
    tree_oid: str
    external_revisions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _sha("head_sha", self.head_sha)
        _sha("tree_oid", self.tree_oid)
        # A copy, so a caller's dict changed later cannot change what is judged.
        object.__setattr__(self, "external_revisions", _partners(self.external_revisions))


@dataclass(frozen=True)
class RunResults:
    """One run's per-job results and what it ran on.

    ``definitions`` is the commit whose definitions the run used: the head for the candidate's
    own run, the default branch's commit for a trusted run. ``needs`` is GitHub's
    ``toJSON(needs)`` text, read only by ``verdict.from_needs``.
    """

    definitions: str
    head_sha: str
    tree_oid: str
    external_revisions: Mapping[str, str]
    needs: str
    toolchain: Mapping[str, str]
    evidence_digests: Mapping[str, str]

    def __post_init__(self) -> None:
        _sha("definitions", self.definitions)
        _sha("head_sha", self.head_sha)
        _sha("tree_oid", self.tree_oid)
        if not isinstance(self.needs, str):
            raise trust.Refused(
                f"needs must be toJSON(needs) text, got {type(self.needs).__name__}"
            )
        object.__setattr__(self, "external_revisions", _partners(self.external_revisions))
        object.__setattr__(self, "toolchain", dict(self.toolchain))
        object.__setattr__(self, "evidence_digests", dict(self.evidence_digests))


@dataclass(frozen=True)
class Decision:
    state: str
    reason: str
    output: Mapping[str, str]
    reasons: tuple[verdict.Reason, ...] = ()
    counted: str | None = None
    receipt: trust.Receipt | None = None


# ── The planned set ───────────────────────────────────────────────────────────────────────────


def _git(git_dir: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(git_dir), *args],
        capture_output=True,
        check=False,
        # As trust's reads: a GIT_DIR or index a calling hook exported must not redirect this.
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    )


def _require_mains_tip(git_dir: Path, revision: str, *, main: str = "origin/main") -> None:
    """Refuse ``revision`` unless it is ``main``'s tip in ``git_dir``.

    ``trust.require_on_main`` first, for its refusals (a name or a short id, a shallow clone, no
    such commit, a commit off main); then the tip itself. An older commit on main is refused
    too: its definitions and its catalogue are not the default branch's any more.
    """
    trust.require_on_main(git_dir, revision, main=main)
    proc = _git(git_dir, "rev-parse", "--verify", "--quiet", f"{main}^{{commit}}")
    tip = proc.stdout.decode("ascii", "replace").strip()
    if proc.returncode != 0 or not tip:
        raise trust.Refused(f"cannot read the tip of {main}")
    if revision != tip:
        raise trust.Refused(f"{revision} is on {main} but is not its tip, {tip}")


def planned_jobs(git_dir: Path, revision: str, *, main: str = "origin/main") -> tuple[str, ...]:
    """The merge tier's job ids in the catalogue of ``revision``, ``main``'s tip.

    Only that commit's blob is read: never the worktree, never a candidate's commit and never an
    older commit on main, which :func:`_require_mains_tip` refuses. A catalogue that is missing or
    that its own rules refuse plans nothing, and is refused.
    """
    _require_mains_tip(git_dir, revision, main=main)
    proc = _git(git_dir, "cat-file", "blob", f"{revision}:{catalogue.CATALOGUE}")
    if proc.returncode != 0:
        raise trust.Refused(f"{revision} has no {catalogue.CATALOGUE}")
    try:
        text = proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise trust.Refused(f"{catalogue.CATALOGUE} at {revision} is not UTF-8") from None
    try:
        loaded = catalogue.load(text, catalogue.CATALOGUE)
    except catalogue.CatalogueError as exc:
        raise trust.Refused("; ".join(exc.problems)) from None
    return tuple(job.id for job in loaded.tier("merge"))


# ── The decision ──────────────────────────────────────────────────────────────────────────────


def _revisions(revisions: Mapping[str, str]) -> str:
    pairs = sorted(revisions.items())
    return "{" + ", ".join(f"{k}={v}" for k, v in pairs) + "}" if pairs else "none"


def _not_green(
    state: str,
    reason: str,
    reasons: Iterable[verdict.Reason] = (),
    counted: str | None = None,
) -> Decision:
    listed = tuple(reasons)
    lines = [reason, ""]
    for r in listed:
        name = f"`{r.key}`" if r.key else "the plan"
        lines.append(f"- {name}: {r.kind}" + (f" ({r.detail})" if r.detail else ""))
    text = "\n".join(lines).rstrip() + "\n"
    output = {"title": f"{merge_verdict.CHECK_NAME}: {state}", "summary": reason, "text": text}
    return Decision(state, reason, output, listed, counted)


def decide(
    plan: Iterable[str],
    job_results: RunResults | None,
    comparison: trust.Comparison,
    identity: Candidate,
    trusted_run: RunResults | None,
    *,
    controller_revision: str,
    git_dir: Path,
    main: str = "origin/main",
) -> Decision:
    """The decision on ``identity``. See the module's rules; every one fails closed.

    ``git_dir`` is a full clone holding ``main``, where ``comparison.base`` must be the tip.
    """
    head = identity.head_sha
    if comparison.head != head:
        return _not_green(
            RED, f"the definitions comparison is of {comparison.head}, not the head {head}"
        )
    try:
        _require_mains_tip(git_dir, comparison.base, main=main)
    except trust.Refused as exc:
        return _not_green(
            RED, f"the definitions comparison's base is not the default branch's commit: {exc}"
        )
    if comparison.trusted:
        counted, which, definitions = job_results, CANDIDATE_RUN, head
        if counted is None:
            return _not_green(PENDING, "the candidate's run has not reported yet")
    else:
        counted, which, definitions = trusted_run, DEFAULT_BRANCH_RUN, comparison.base
        if counted is None:
            changed = ", ".join(c.path for c in comparison.changes)
            return _not_green(
                PENDING,
                f"the candidate changed definitions ({changed}), so its own run is advisory;"
                " the run of the default branch's definitions on its tree has not reported yet",
            )
    if counted.head_sha != head:
        return _not_green(
            PENDING,
            f"the {which} run is of an older head {counted.head_sha}, not {head}",
            counted=which,
        )
    if counted.definitions != definitions:
        return _not_green(
            PENDING,
            f"the {which} run used the definitions of {counted.definitions}, not those of"
            f" {definitions}",
            counted=which,
        )
    if counted.tree_oid != identity.tree_oid:
        return _not_green(
            RED,
            f"the {which} run names tree {counted.tree_oid} for head {head}, whose tree is"
            f" {identity.tree_oid}",
            counted=which,
        )
    if dict(counted.external_revisions) != dict(identity.external_revisions):
        return _not_green(
            PENDING,
            f"the {which} run used partner revisions {_revisions(counted.external_revisions)},"
            f" and the current partner revisions are {_revisions(identity.external_revisions)}",
            counted=which,
        )
    run_identity = verdict.Identity(counted.head_sha, counted.external_revisions)
    candidate = verdict.Identity(head, identity.external_revisions)
    try:
        results = from_needs(counted.needs, run_identity)
    except verdict.Refused as exc:
        return _not_green(RED, f"the {which} run's results cannot be read: {exc}", counted=which)
    planned = tuple(plan)
    # Beside a plan, never in place of one: an empty plan stays red as empty.
    required = (*planned, CATALOGUE_CHECK) if planned else planned
    try:
        judged = verdict.decide(required, results, candidate)
    except verdict.Refused as exc:
        return _not_green(RED, f"the plan cannot be read: {exc}", counted=which)
    if not judged.green:
        return _not_green(
            RED,
            f"{len(judged.reasons)} job(s) fell short in the {which} run",
            judged.reasons,
            which,
        )
    try:
        receipt = trust.receipt_from(
            {
                "controller_revision": controller_revision,
                "head_sha": head,
                "tree_oid": identity.tree_oid,
                "external_revisions": dict(identity.external_revisions),
                "toolchain": dict(counted.toolchain),
                "evidence_digests": dict(counted.evidence_digests),
            }
        )
    except trust.Refused as exc:
        return _not_green(RED, f"the {which} run passed, but {exc}", counted=which)
    reason = f"every planned job passed in the {which} run on {head} and tree {identity.tree_oid}"
    output = merge_verdict.render_output(
        receipt, title=f"{merge_verdict.CHECK_NAME}: {GREEN}", summary=reason
    )
    return Decision(GREEN, reason, output, (), which, receipt)
