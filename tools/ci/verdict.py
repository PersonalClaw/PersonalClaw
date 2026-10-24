"""One verdict over a plan's jobs: green only when every planned job did its work on this candidate.

:func:`decide` reads a plan (the job keys that had to run), the results it has, and the candidate
they must be for. The verdict is green only when all of these hold:

- the plan is not empty;
- every planned key has a result;
- every result for that key, on every attempt, is ``success``;
- every one of those results names the candidate's head and the candidate's partner revisions
  (:class:`Identity`), so a result for an older head, or an older head of a partner repository,
  is not this candidate's. A per-job record names only the partners its own job read
  (``JobResult.own_partners``): apps-contract's names the apps checkout, every other job's
  names none. So given each planned key's partners (``key_partners``, which the plan reads from
  the job's catalogue entry), a record must name exactly its key's, each one the candidate
  names, at the candidate's revision: a record of a job that reads a partner and names none, or
  one naming a partner its job does not read, is stale. Given no partners by key, a record is
  held to the candidate's partners exactly, as a ``needs`` result, which carries the run's
  whole identity, always is.

Anything else is red, with one :class:`Reason` per key that fell short: ``missing``,
``cancelled``, ``skipped``, ``failure``, ``unknown result``, ``no identity``, ``stale head``,
``stale partner``, or ``red on an earlier attempt`` (a red is never rerun into green). A job
outside the plan that was skipped or passed changes nothing; one that failed, was cancelled or
reported a word this module does not know, on any attempt, reads red, so the verdict fails closed.

A result read from a record carries the record's failed tests (:class:`FailedTest`): each one the
diagnostic rerun classified :data:`FLAKE` or :data:`REPEATED_FAILURE`, or left
:data:`NOT_CLASSIFIED`, and each quarantined failure (:data:`QUARANTINED`). :func:`decide` lists
every one of them in the verdict, and reads them for nothing else: the first result is the result,
so a job whose only failure is a flake reads red as a failure, and a pass that a quarantine entry
waived a failure in reads green. Only that entry waives a known failure.

Given the run's id (``run_id``, when the results are one run's own records, :func:`read_records`),
:func:`decide` reads two more reds: a result of another run (``another run``) or of none (``no
run``: a local record), and a planned key with no result on attempt 1 (``missing on attempt 1``),
so a rerun carries only what attempt 1 recorded and never fills a record attempt 1 left empty.

Planned keys are opaque strings: a job id from ``needs``, a matrix entry from a per-job verdict
file, or a job the controller read from the API all mean the same thing here.

:func:`from_needs` reads GitHub's ``toJSON(needs)``: an object of jobs, each named once and
holding exactly ``result`` (a string) and ``outputs`` (an object). It refuses anything else.
``needs`` carries no identity, so the caller stamps the run's own: on a pull request that is
``github.event.pull_request.head.sha``, never ``github.sha``, which is the test-merge commit.
``needs`` holds each job's latest attempt only, so a red on an earlier attempt is not in it.

``python -m tools.ci verdict`` (or ``python3 -I tools/ci/verdict.py``, the way a trusted job runs
the default branch's copy) reads a plan file and a needs file and exits 0 green, 1 red, 2 on input
it cannot read. Every usage error is input it cannot read: there is no ``-h``, since a help page
would exit 0 having judged nothing, and an option given twice is refused, not last-wins. When
``$GITHUB_STEP_SUMMARY`` is set, the verdict's reasons, or why the input could not be read, are
appended to it. ``--records`` adds one run's per-job records (:func:`read_records`), with
``--run-id`` naming that run and ``--key-partners`` each planned key's partners (a JSON object of
key to partner names, with --records only), and ``--attempt`` names the run's attempt.

:func:`job_record` writes, and :func:`from_job_records` reads, the per-job record a run of a
catalogue job leaves (``python -m tools.ci run-job``): its key, result, identity, the GitHub run
that wrote it (none for a local run), attempt, the diagnostic rerun of a red job's failed tests
(each classified :data:`FLAKE` or :data:`REPEATED_FAILURE`, never turning the result green),
which quarantined tests failed, and each step's argv, exit and seconds.

Standard library only, with no relative imports, so a trusted job can run it as a script.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import NoReturn

#: The result words GitHub gives a job: in ``needs``, on a check run's conclusion and in the API.
SUCCESS = "success"
FAILURE = "failure"
CANCELLED = "cancelled"
SKIPPED = "skipped"
WORDS = frozenset({SUCCESS, FAILURE, CANCELLED, SKIPPED})

#: A full object id, SHA-1 or SHA-256, as ``tools/ci/trust.py``'s receipts carry. Matched with
#: ``fullmatch``, so nothing may follow the id, not even a newline.
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


class Refused(ValueError):
    """The input cannot be read as what it claims to be. The message says why."""


@dataclass(frozen=True)
class Identity:
    """What a result was measured on: the candidate's head, and each partner repository's head.

    The field names are ``tools/ci/trust.py``'s receipt fields. ``external_revisions`` may be
    empty, for a candidate that reads no other repository.
    """

    head_sha: str
    external_revisions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.head_sha, str) or not _SHA.fullmatch(self.head_sha):
            raise Refused(f"head_sha must be a full object id, got {self.head_sha!r}")
        revisions = dict(self.external_revisions)
        for name, sha in revisions.items():
            if not isinstance(name, str) or not name:
                raise Refused("a partner revision must be named")
            if not isinstance(sha, str) or not _SHA.fullmatch(sha):
                raise Refused(f"partner {name} must be a full object id, got {sha!r}")
        # A copy, so a caller's dict changed later cannot change what was judged.
        object.__setattr__(self, "external_revisions", revisions)


@dataclass(frozen=True)
class FailedTest:
    """One failed test a job's record names, and how it stands: :data:`FLAKE` or
    :data:`REPEATED_FAILURE` (the diagnostic rerun's word), :data:`NOT_CLASSIFIED` (it was not
    rerun, or the rerun said nothing of it), or :data:`QUARANTINED` (it failed under a quarantine
    entry). ``why`` is the record's reason. None of them changes its job's result."""

    key: str
    attempt: int
    test: str
    standing: str
    why: str = ""


@dataclass(frozen=True)
class JobResult:
    """One job's result on one attempt. ``identity`` is None when the result named none, and
    ``run_id`` when it named no run (a local record, or a ``needs`` result). ``tests`` are the
    failed tests its record names, in the record's order; a ``needs`` result names none.
    ``own_partners`` is True for a per-job record, whose identity names only the partners its
    own job read (held to its key's partners when :func:`decide` is given them), and False for
    a ``needs`` result, whose identity is the run's whole one."""

    key: str
    result: str
    identity: Identity | None
    attempt: int = 1
    run_id: int | None = None
    tests: tuple[FailedTest, ...] = ()
    own_partners: bool = False
    #: What a per-job record says the job ran on (:data:`RUNNER_KEYS`) and its toolchain's
    #: identities, by toolchain word; None for a record that names neither (an older schema's, or
    #: one written without them) and for a ``needs`` result.
    runner: Mapping[str, str] | None = None
    toolchain: Mapping[str, str] | None = None


@dataclass(frozen=True)
class Reason:
    """Why one key reads red. ``key`` is empty for a reason about the whole plan."""

    key: str
    kind: str
    detail: str = ""


@dataclass(frozen=True)
class Verdict:
    green: bool
    reasons: tuple[Reason, ...] = ()
    #: On a push to main only (:func:`decide_main_push`): the newer commit whose run replaced
    #: this one, when that is the whole of why it is red. Empty everywhere else. Never green.
    superseded_by: str = ""
    #: Every failed test the results' records name, by key, attempt and test: listed, and never
    #: read for green or red.
    tests: tuple[FailedTest, ...] = ()


def _fault(
    result: JobResult,
    candidate: Identity,
    run_id: int | None = None,
    reads: frozenset[str] | None = None,
) -> tuple[str, str] | None:
    """What is wrong with one planned result, or None when it counts for the candidate (and,
    when ``run_id`` is given, for that run). ``reads`` is the partners the result's own job
    reads, given for a key whose partners the plan names."""
    if result.result not in WORDS:
        return "unknown result", f"GitHub gives no job the result {result.result!r}"
    if result.result != SUCCESS:
        return result.result, f"attempt {result.attempt}"
    if run_id is not None and result.run_id is None:
        return "no run", "the result names no run, so it cannot be this run's"
    if run_id is not None and result.run_id != run_id:
        return "another run", f"recorded by run {result.run_id}, not run {run_id}"
    if result.identity is None:
        return "no identity", "the result names no head, so it cannot be this candidate's"
    if result.identity.head_sha != candidate.head_sha:
        return "stale head", f"ran on {result.identity.head_sha}, not {candidate.head_sha}"
    if result.own_partners and reads is not None:
        # Exactly the partners its own job reads, each at the candidate's revision: a job that
        # reads a partner and names none is as stale as one naming a partner it does not read.
        named = result.identity.external_revisions
        for name in sorted(set(named) - reads):
            return "stale partner", (
                f"ran with partner {name}={named[name]}, which its job does not read"
            )
        for name in sorted(reads - set(named)):
            return "stale partner", f"names no {name}, which its job reads"
        for name in sorted(reads):
            want = candidate.external_revisions.get(name)
            if want is None:
                return "stale partner", (
                    f"its job reads {name}, which the candidate ({_revisions(candidate)})"
                    " does not name"
                )
            if named[name] != want:
                return "stale partner", (
                    f"ran with partner {name}={named[name]}, not {name}={want}"
                )
    elif dict(result.identity.external_revisions) != dict(candidate.external_revisions):
        return "stale partner", (
            f"ran with partners {_revisions(result.identity)}, not {_revisions(candidate)}"
        )
    return None


def _revisions(identity: Identity) -> str:
    pairs = sorted(identity.external_revisions.items())
    return "{" + ", ".join(f"{k}={v}" for k, v in pairs) + "}" if pairs else "none"


def _planned(
    results: Sequence[JobResult],
    candidate: Identity,
    run_id: int | None = None,
    reads: frozenset[str] | None = None,
) -> tuple[str, str] | None:
    """The fault of one planned key's results, or None when every attempt was green here."""
    if not results:
        return "missing", "no result for a planned job"
    if run_id is not None and all(r.attempt != 1 for r in results):
        # Every planned entry ran on attempt 1, so its record is attempt 1's: a later attempt's
        # result alone is a rerun filling a record attempt 1 never wrote.
        return "missing on attempt 1", "a rerun carries only what attempt 1 recorded"
    latest = max(r.attempt for r in results)
    for r in results:
        if r.attempt == latest:
            fault = _fault(r, candidate, run_id, reads)
            if fault:
                return fault
    for r in sorted(results, key=lambda r: r.attempt):
        fault = _fault(r, candidate, run_id, reads)
        if fault:
            return "red on an earlier attempt", f"attempt {r.attempt}: {fault[0]}; {fault[1]}"
    return None


def _unplanned(results: Sequence[JobResult]) -> tuple[str, str] | None:
    """A job outside the plan reads red when it failed, was cancelled, or said something unknown."""
    for r in sorted(results, key=lambda r: r.attempt):
        if r.result in (SUCCESS, SKIPPED):
            continue
        kind = r.result if r.result in WORDS else "unknown result"
        return f"unplanned {kind}", f"attempt {r.attempt}: a job outside the plan said {r.result!r}"
    return None


def _key_partners(
    key_partners: Mapping[str, Iterable[str]], plan: Sequence[str]
) -> dict[str, frozenset[str]]:
    """``key_partners`` read for ``plan``: every planned key and no other, each a list of
    partner names, none twice. Anything else is refused, so no key is left unheld."""
    if not isinstance(key_partners, Mapping):
        raise Refused("the partners by key must be a mapping of each planned key to its partners")
    out: dict[str, frozenset[str]] = {}
    for key, names in key_partners.items():
        if key not in plan:
            raise Refused(f"the partners by key name partners for {key}, which is not planned")
        if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
            raise Refused(f"the partners of {key} must be a list of partner names")
        if len(set(names)) != len(names):
            raise Refused(f"the partners of {key} name one partner twice")
        out[key] = frozenset(names)
    missing = [key for key in plan if key not in out]
    if missing:
        raise Refused(f"the partners by key name no partners for {', '.join(missing)}")
    return out


def decide(
    planned: Iterable[str],
    results: Iterable[JobResult],
    candidate: Identity,
    *,
    run_id: int | None = None,
    key_partners: Mapping[str, Iterable[str]] | None = None,
) -> Verdict:
    """The verdict on ``candidate``: green only when every planned job did its work on it.

    ``run_id`` is given when ``results`` are one run's own: each planned result must then name
    that run, and each planned key must have a result on attempt 1. ``key_partners`` names, for
    every planned key and no other, the partners its own job reads: each record of that key must
    name exactly those, at the candidate's revisions. Without it, a record is held to the
    candidate's partners exactly."""
    if run_id is not None and (not _whole(run_id) or run_id < 1):
        raise Refused(f"run_id must be a whole number from 1, got {run_id!r}")
    plan: list[str] = []
    for key in planned:
        if not isinstance(key, str) or not key:
            raise Refused(f"a planned job key must be a non-empty string, got {key!r}")
        if key not in plan:
            plan.append(key)
    reads = None if key_partners is None else _key_partners(key_partners, plan)
    by_key: dict[str, list[JobResult]] = {}
    tests: list[FailedTest] = []
    for r in results:
        by_key.setdefault(r.key, []).append(r)
        tests += r.tests
    reasons: list[Reason] = []
    if not plan:
        reasons.append(Reason("", "empty plan", "a plan with no jobs proves nothing"))
    for key in plan:
        fault = _planned(
            by_key.get(key, []), candidate, run_id, None if reads is None else reads[key]
        )
        if fault:
            reasons.append(Reason(key, *fault))
    for key in sorted(set(by_key) - set(plan)):
        fault = _unplanned(by_key[key])
        if fault:
            reasons.append(Reason(key, *fault))
    # The tests are listed, never judged: the green above is read from the results alone.
    listed = tuple(sorted(tests, key=lambda t: (t.key, t.attempt, t.test)))
    return Verdict(green=not reasons, reasons=tuple(reasons), tests=listed)


#: The reason a push to main's planned job reads red when a newer push's run replaced it.
SUPERSEDED = "superseded"


#: The one run the main-push reading accepts: a push, to the full ref of main.
MAIN_PUSH = ("push", "refs/heads/main")


def decide_main_push(
    planned: Iterable[str],
    results: Iterable[JobResult],
    pushed: Identity,
    proven: Mapping[str, str],
    *,
    event: str,
    ref: str,
) -> Verdict:
    """The verdict on a push to ``main``: :func:`decide`, with one more name for a red.

    ``event`` and ``ref`` are the run's own (``github.event_name`` and ``github.ref``), and
    anything but a push to ``refs/heads/main`` is refused, so a pull request's run, a
    ``pull_request_target`` run on main's ref or a tag named ``main`` can never read a job as
    superseded. ``proven`` maps a planned key to the newer commit whose run replaced it, as
    ``tools/ci/superseded.py`` proved from the API. A key whose result is ``cancelled`` and that
    ``proven`` names reads ``superseded`` instead of ``cancelled``. It is still red: #3915 leaves
    a commit in the middle of a burst unverified on its own. ``superseded_by`` names the newer
    commit only when every reason is superseded by that one commit. A failed, skipped or missing
    job keeps its own reason whatever ``proven`` says. A candidate's reading is :func:`decide`,
    which takes no proof, so a pull request's cancelled job is never superseded.
    """
    if (event, ref) != MAIN_PUSH:
        raise Refused(
            f"only a push to refs/heads/main may read a job as superseded, not a {event!r} run "
            f"on {ref!r}"
        )
    checked: dict[str, str] = {}
    for key, newer in proven.items():
        if not isinstance(key, str) or not key:
            raise Refused(f"a superseded key must be a non-empty string, got {key!r}")
        if not isinstance(newer, str) or not _SHA.fullmatch(newer):
            raise Refused(
                f"the commit that superseded {key} must be a full object id, got {newer!r}"
            )
        if newer == pushed.head_sha:
            raise Refused(f"the commit that superseded {key} is the pushed commit")
        checked[key] = newer
    base = decide(planned, results, pushed)
    if base.green:
        return base
    reasons = tuple(
        (
            Reason(r.key, SUPERSEDED, f"by {checked[r.key]}")
            if r.kind == CANCELLED and r.key in checked
            else r
        )
        for r in base.reasons
    )
    by = {checked[r.key] for r in reasons if r.kind == SUPERSEDED}
    whole = len(by) == 1 and all(r.kind == SUPERSEDED for r in reasons)
    return Verdict(
        green=False,
        reasons=reasons,
        superseded_by=by.pop() if whole else "",
        tests=base.tests,
    )


def _no_repeats_in(what: str) -> Callable[[list[tuple[str, object]]], dict[str, object]]:
    """A JSON object reader that refuses a repeated key, naming *what* it reads."""

    def no_repeats(pairs: list[tuple[str, object]]) -> dict[str, object]:
        # JSON would keep a repeated key's last value, so a job's failure could be followed by
        # its success. GitHub never writes a key twice.
        out: dict[str, object] = {}
        for key, value in pairs:
            if key in out:
                raise Refused(f"{what} repeats the key {key!r}")
            out[key] = value
        return out

    return no_repeats


_no_repeats = _no_repeats_in("needs")


def from_needs(text: str, identity: Identity, attempt: int = 1) -> list[JobResult]:
    """The results in GitHub's ``toJSON(needs)``, each stamped with the run's ``identity`` and
    ``attempt``. They name no run: with a run's records, no ``needs`` job may answer a planned
    key (``verdict --records``), so a ``needs`` result is only ever read as outside the plan.

    ``needs`` holds each needed job's latest result only; a red on an earlier attempt of the
    same run is the controller's to read from the API.
    """
    try:
        data = json.loads(text, object_pairs_hook=_no_repeats)
    except Refused:
        raise
    except RecursionError:
        raise Refused("needs is nested too deeply to read") from None
    except ValueError as exc:
        raise Refused(f"needs is not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise Refused(f"needs must be an object of jobs, got {type(data).__name__}")
    out: list[JobResult] = []
    for key in sorted(data):
        job = data[key]
        if not key:
            raise Refused("needs names a job with an empty id")
        if not isinstance(job, dict):
            raise Refused(f"needs.{key} must be an object, got {type(job).__name__}")
        if set(job) != {"result", "outputs"}:
            raise Refused(f"needs.{key} must hold exactly result and outputs, got {sorted(job)}")
        if not isinstance(job["result"], str):
            raise Refused(f"needs.{key} has no result string")
        if not isinstance(job["outputs"], dict):
            raise Refused(f"needs.{key}.outputs must be an object")
        out.append(JobResult(key, job["result"], identity, attempt))
    return out


#: The per-job record ``python -m tools.ci run-job`` writes: its schema name, and its fields.
#: Version 3 adds what the job ran on and its toolchain (#3919), which a push to main holds a
#: replayed pass to; a version-2 record is still read, and names neither.
JOB_RECORD_SCHEMA = "personalclaw.ci.job-record/3"
_SCHEMA_2 = "personalclaw.ci.job-record/2"
_RECORD_KEYS_2 = frozenset(
    {
        "schema",
        "key",
        "result",
        "head_sha",
        "external_revisions",
        "run_id",
        "attempt",
        "rerun",
        "quarantined",
        "plan",
        "workers",
        "steps",
    }
)
_RECORD_KEYS = _RECORD_KEYS_2 | {"runner", "toolchain"}
#: What a record's ``runner`` names: the catalogue's runner label the job ran under, the image it
#: ran on (``<ImageOS>/<ImageVersion>`` on a GitHub-hosted runner, the container image for a job
#: in one, empty for a bare host that names none), and the OS and architecture.
RUNNER_KEYS = frozenset({"label", "image", "os", "arch"})
_STEP_RECORD_KEYS = frozenset({"argv", "exit", "seconds"})
_RERUN_KEYS = frozenset({"argv", "exit", "tests"})
_RERUN_TEST_KEYS = frozenset({"test", "rerun_result", "why"})

#: How the diagnostic rerun classifies a failed test: it passed alone in a fresh process, or it
#: failed again. A test that was not rerun (a rail, a security-marked test) is classified null.
#: Neither word turns a red green: the first result is the result.
FLAKE = "flake"
REPEATED_FAILURE = "repeated failure"
RERUN_RESULTS = frozenset({FLAKE, REPEATED_FAILURE})
#: How a failed test stands in a verdict besides the rerun's two words: one the rerun did not
#: classify, and one that failed under a quarantine entry, which waived it in a job that passed.
NOT_CLASSIFIED = "not classified"
QUARANTINED = "quarantined"
#: Why a quarantined failure is listed and not red.
WAIVED = "a quarantine entry waived it"
#: Where a sharded job's file list came from: CI's own plan, or a local run's even split.
PLANS = frozenset({"ci", "offline"})


def job_record(
    *,
    key: str,
    result: str,
    identity: Identity,
    steps: Sequence[tuple[Sequence[str], int, float]],
    attempt: int = 1,
    run_id: int | None = None,
    rerun: Mapping[str, object] | None = None,
    quarantined: Sequence[str] = (),
    plan: str | None = None,
    workers: int | None = None,
    runner: Mapping[str, str] | None = None,
    toolchain: Mapping[str, str] | None = None,
) -> str:
    """One job's (or one shard's) result as the JSON :func:`from_job_records` reads.

    ``run_id`` is the GitHub run that wrote it, or None for a local record, which names no run.
    ``steps`` are each step's argv as run, its exit status and its seconds. ``rerun`` is the
    diagnostic rerun of a red job's failed tests, or None when there was no failed test to
    diagnose: the rerun's ``argv`` and ``exit`` (both null when no failed test could be rerun),
    and each failed test with its ``rerun_result`` (:data:`FLAKE`, :data:`REPEATED_FAILURE`, or
    null when it was not rerun) and ``why``. Only a failure carries one, and it never changes
    ``result``. ``quarantined`` names the quarantined tests that failed, for a reader that keeps
    only a clean pass. ``plan`` is where a sharded job's list came from (one of :data:`PLANS`),
    ``workers`` a worker count a local run set in place of the catalogue's. ``runner`` is what the
    job ran on (:data:`RUNNER_KEYS`) and ``toolchain`` its toolchain's identities; either is
    written null when not given, and a record that names none is never replayed on a push.
    """
    record: dict[str, object] = {
        "schema": JOB_RECORD_SCHEMA,
        "key": key,
        "result": result,
        "head_sha": identity.head_sha,
        "external_revisions": dict(sorted(identity.external_revisions.items())),
        "run_id": run_id,
        "attempt": attempt,
        # A copy of a mapping; anything else is written as given, so the reader refuses it by name.
        "rerun": dict(rerun) if isinstance(rerun, Mapping) else rerun,
        "quarantined": list(quarantined),
        "plan": plan,
        "workers": workers,
        "runner": dict(runner) if isinstance(runner, Mapping) else runner,
        "toolchain": (
            dict(sorted(toolchain.items())) if isinstance(toolchain, Mapping) else toolchain
        ),
        "steps": [
            {"argv": list(argv), "exit": code, "seconds": round(seconds, 3)}
            for argv, code, seconds in steps
        ],
    }
    # Written as JSON a reader in any language takes (no NaN or Infinity), then read back: a
    # record this module would refuse is never written.
    try:
        text = json.dumps(record, sort_keys=True, indent=1, allow_nan=False) + "\n"
    except ValueError as exc:
        raise Refused(f"job record {key!r} is not JSON: {exc}") from None
    from_job_records([text])
    return text


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _no_constant_in(what: str) -> Callable[[str], object]:
    """A reader of JSON's non-standard constants that refuses each, naming *what* it reads."""

    def no_constant(name: str) -> object:
        raise Refused(f"{what} holds {name}, which is not JSON")

    return no_constant


def _rerun_faults(rerun: object, where: str) -> None:
    """Refuse a record's ``rerun`` that is not null or the one shape :func:`job_record` writes."""
    if rerun is None:
        return
    if not isinstance(rerun, dict):
        raise Refused(f"{where}: rerun must be null or an object")
    if set(rerun) != _RERUN_KEYS:
        raise Refused(f"{where}: rerun must hold exactly argv, exit and tests, got {sorted(rerun)}")
    argv, code, tests = rerun["argv"], rerun["exit"], rerun["tests"]
    if argv is not None and (
        not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv)
    ):
        raise Refused(f"{where}: rerun argv must be null or a non-empty list of strings")
    if code is not None and not _whole(code):
        raise Refused(f"{where}: rerun exit must be null or a whole number")
    if (argv is None) != (code is None):
        raise Refused(f"{where}: rerun argv and exit are both null or both set")
    if not isinstance(tests, list) or not tests:
        raise Refused(f"{where}: rerun names no test")
    seen: set[str] = set()
    for entry in tests:
        if not isinstance(entry, dict) or set(entry) != _RERUN_TEST_KEYS:
            raise Refused(f"{where}: each rerun test holds exactly test, rerun_result and why")
        test, result, why = entry["test"], entry["rerun_result"], entry["why"]
        if not isinstance(test, str) or not test:
            raise Refused(f"{where}: a rerun test must name a test id")
        if test in seen:
            raise Refused(f"{where}: rerun names {test} twice")
        seen.add(test)
        if result is not None and (not isinstance(result, str) or result not in RERUN_RESULTS):
            raise Refused(
                f"{where}: {test}'s rerun_result is {result!r}, not {FLAKE!r},"
                f" {REPEATED_FAILURE!r} or null"
            )
        if result is not None and argv is None:
            raise Refused(f"{where}: {test} is classified, and no rerun ran")
        if not isinstance(why, str) or not why:
            raise Refused(f"{where}: {test}'s why must be a non-empty string")


def _one_record(text: str, n: int) -> JobResult:
    where = f"job record {n}"
    try:
        data = json.loads(
            text, object_pairs_hook=_no_repeats_in(where), parse_constant=_no_constant_in(where)
        )
    except Refused:
        raise
    except RecursionError:
        raise Refused(f"{where} is nested too deeply to read") from None
    except ValueError as exc:
        raise Refused(f"{where} is not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise Refused(f"{where} must be an object, got {type(data).__name__}")
    # A version-2 record holds version 2's fields; any other is held to this version's.
    schema = data.get("schema")
    keys = _RECORD_KEYS_2 if schema == _SCHEMA_2 else _RECORD_KEYS
    if set(data) != keys:
        raise Refused(f"{where} must hold exactly {sorted(keys)}, got {sorted(data)}")
    if schema not in (JOB_RECORD_SCHEMA, _SCHEMA_2):
        raise Refused(f"{where} has schema {schema!r}, not {JOB_RECORD_SCHEMA!r} or {_SCHEMA_2!r}")
    key = data["key"]
    if not isinstance(key, str) or not key:
        raise Refused(f"{where} names no job key")
    where = f"job record {key!r}"
    if not isinstance(data["result"], str) or data["result"] not in WORDS:
        raise Refused(f"{where} has the result {data['result']!r}, which is no job's")
    revisions = data["external_revisions"]
    if not isinstance(revisions, dict):
        raise Refused(f"{where}: external_revisions must be an object")
    identity = Identity(data["head_sha"], revisions)
    run_id = data["run_id"]
    if run_id is not None and (not _whole(run_id) or run_id < 1):
        raise Refused(f"{where}: run_id must be a whole number from 1, or null")
    if not _whole(data["attempt"]) or data["attempt"] < 1:
        raise Refused(f"{where}: attempt must be a whole number from 1")
    _rerun_faults(data["rerun"], where)
    # run-job reruns only the failed tests of a red step, so only a failure names a rerun.
    if data["result"] != FAILURE and data["rerun"] is not None:
        raise Refused(f"{where}: only a failure has a rerun, and this {data['result']} names one")
    quarantined = data["quarantined"]
    if not isinstance(quarantined, list) or not all(isinstance(q, str) and q for q in quarantined):
        raise Refused(f"{where}: quarantined must be a list of test ids")
    if data["plan"] is not None and (
        not isinstance(data["plan"], str) or data["plan"] not in PLANS
    ):
        raise Refused(f"{where}: plan is {data['plan']!r}, not one of {sorted(PLANS)} or null")
    workers = data["workers"]
    if workers is not None and (not _whole(workers) or workers < 1):
        raise Refused(f"{where}: workers must be a whole number from 1, or null")
    steps = data["steps"]
    if not isinstance(steps, list):
        raise Refused(f"{where}: steps must be a list")
    for step in steps:
        if not isinstance(step, dict) or set(step) != _STEP_RECORD_KEYS:
            raise Refused(f"{where}: each step holds exactly argv, exit and seconds")
        argv = step["argv"]
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise Refused(f"{where}: a step's argv must be a non-empty list of strings")
        if not _whole(step["exit"]):
            raise Refused(f"{where}: a step's exit must be a whole number")
        seconds = step["seconds"]
        if not _finite(seconds) or seconds < 0:
            raise Refused(f"{where}: a step's seconds must be a number, finite and 0 or more")
    if data["result"] == SUCCESS and any(step["exit"] != 0 for step in steps):
        raise Refused(f"{where} says success, and a step exited non-zero")
    runner = data.get("runner")
    if runner is not None and (
        not isinstance(runner, dict)
        or set(runner) != RUNNER_KEYS
        or not all(isinstance(v, str) for v in runner.values())
        or not all(runner[k] for k in ("label", "os", "arch"))
    ):
        raise Refused(
            f"{where}: runner must be null or name exactly {sorted(RUNNER_KEYS)} as strings,"
            " the label, OS and architecture not empty"
        )
    toolchain = data.get("toolchain")
    if toolchain is not None and (
        not isinstance(toolchain, dict)
        or not all(isinstance(k, str) and k and isinstance(v, str) for k, v in toolchain.items())
    ):
        raise Refused(f"{where}: toolchain must be null or an object of strings, by toolchain word")
    attempt = data["attempt"]
    tests = [
        FailedTest(key, attempt, t["test"], t["rerun_result"] or NOT_CLASSIFIED, t["why"])
        for t in (data["rerun"] or {}).get("tests", [])
    ]
    tests += [FailedTest(key, attempt, test, QUARANTINED, WAIVED) for test in quarantined]
    return JobResult(
        key, data["result"], identity, attempt, run_id, tuple(tests), True, runner, toolchain
    )


def from_job_records(texts: Iterable[str]) -> list[JobResult]:
    """The results in the per-job records :func:`job_record` writes, one text per record.

    Like :func:`from_needs` it refuses what it cannot read as a record: a repeated key, NaN or
    Infinity, a field missing or unknown, a result word that is no job's, an object id that is
    not a full one, an attempt that is not a whole number from 1, a run id that is neither that
    nor null, a step's seconds that are not a finite number, a success with a step that
    failed, or a rerun on a record that is not a failure. Two records for the same key and attempt
    are refused, so a failure cannot be followed by a success that hides it.
    """
    out: list[JobResult] = []
    seen: set[tuple[str, int]] = set()
    for n, text in enumerate(texts, 1):
        result = _one_record(text, n)
        if (result.key, result.attempt) in seen:
            raise Refused(f"two job records for {result.key!r} on attempt {result.attempt}")
        seen.add((result.key, result.attempt))
        out.append(result)
    return out


#: The prefix of every per-job record artifact a workflow's test stage uploads.
RECORD_ARTIFACT_PREFIX = "ci-record-"

#: The most a record file may hold. A record is a few kilobytes; anything far larger is not one.
#: The telemetry collector reads a record within it too.
RECORD_BYTES = 1_000_000

#: A run attempt as text (``github.run_attempt``, ``$GITHUB_RUN_ATTEMPT``): ASCII digits, 1 to
#: 999999. ``tools/ci/jobs.py`` reads the environment with it too.
ATTEMPT_TEXT = re.compile(r"[1-9][0-9]{0,5}")

#: A run id as text (``github.run_id``, ``$GITHUB_RUN_ID``): ASCII digits, from 1, at most 19 of
#: them, so it fits the 64-bit id GitHub gives a run.
RUN_ID_TEXT = re.compile(r"[1-9][0-9]{0,18}")


def record_slug(key: str) -> str:
    """A record key as its artifact's name spells it: lowercase, with each run of anything but a
    letter, a digit or ``-`` one ``-`` (``python-suite (2/4)`` is ``python-suite-2-4``). The plan's
    matrix carries it, since a workflow cannot work it out from the key. A key with nothing left
    is refused."""
    slug = re.sub(r"[^a-z0-9-]+", "-", key.lower()).strip("-") if isinstance(key, str) else ""
    if not slug:
        raise Refused(f"a record's key must name a job, got {key!r}")
    return slug


def record_artifact(key: str, *, run_id: int, attempt: int) -> str:
    """The artifact name a test-stage entry uploads its record under: the key's slug, the run and
    the attempt, so a rerun's records never overwrite the first attempt's. :func:`read_records`
    reads a record only from the folder this names for its own key and attempt in the run it
    judges, so another run's artifact, or a renamed one, is refused."""
    slug = record_slug(key)
    for name, value in (("run_id", run_id), ("attempt", attempt)):
        if not _whole(value) or value < 1:
            raise Refused(
                f"a record artifact's {name} must be a whole number from 1, got {value!r}"
            )
    return f"{RECORD_ARTIFACT_PREFIX}{slug}-{run_id}-{attempt}"


def read_records(folder: str, *, run_id: int, attempt: int) -> list[JobResult]:
    """The records run ``run_id``'s test stage uploaded, as ``download-artifact`` lays them out in
    ``folder``: one folder per artifact, each holding one ``.json`` record, read with
    :func:`from_job_records`. Each record must sit in the folder :func:`record_artifact` names for
    its key and attempt in this run.

    Refused: a folder that cannot be read; a link anywhere in it; a file beside the artifact
    folders; an artifact folder holding anything but one record file; a file too large to be a
    record; a record the reader refuses; a record in a folder its key and attempt do not name in
    this run (another run's artifact, a renamed one, or a second upload of one record, since a key
    and attempt name one folder); and a record written by a later attempt than ``attempt`` (the
    run's own), since a rerun's record must never stand in an earlier attempt's reading. A record
    naming another run, or none, is read as it is, and :func:`decide` given ``run_id`` reads it
    red."""
    for name, value in (("run_id", run_id), ("attempt", attempt)):
        if not _whole(value) or value < 1:
            raise Refused(f"the records' {name} must be a whole number from 1, got {value!r}")
    if not os.path.isdir(folder) or os.path.islink(folder):
        raise Refused(f"cannot read the records folder {folder}: not a folder")
    out: list[JobResult] = []
    for top in _entries(folder):
        if not top.is_dir(follow_symlinks=False):
            raise Refused(f"{top.path} is not an artifact's folder")
        inner = _entries(top.path)
        if len(inner) != 1:
            raise Refused(f"{top.path} holds {len(inner)} entries, not one record")
        [entry] = inner
        if not entry.is_file(follow_symlinks=False) or not entry.name.endswith(".json"):
            raise Refused(f"{entry.path} is not a record file")
        try:
            [result] = from_job_records([_record_text(entry.path)])
        except Refused as exc:
            raise Refused(f"{entry.path}: {exc}") from None
        expected = record_artifact(result.key, run_id=run_id, attempt=result.attempt)
        if top.name != expected:
            raise Refused(
                f"{top.name} holds the record of {result.key!r} on attempt {result.attempt},"
                f" which this run uploads as {expected}"
            )
        if result.attempt > attempt:
            raise Refused(
                f"job record {result.key!r} was written by attempt {result.attempt}, after this"
                f" run's attempt {attempt}"
            )
        out.append(result)
    return out


def _entries(folder: str) -> list[os.DirEntry[str]]:
    """What ``folder`` holds, by name, refusing a link or a folder it cannot read."""
    try:
        with os.scandir(folder) as listing:
            entries = sorted(listing, key=lambda e: e.name)
    except OSError as exc:
        raise Refused(f"cannot read the records folder {folder}: {exc}") from None
    for entry in entries:
        if entry.is_symlink():
            raise Refused(f"the records folder holds a link: {entry.path}")
    return entries


def _record_text(path: str) -> str:
    """One record file's text: never through a link, and never more than a record can hold."""
    try:
        if os.stat(path, follow_symlinks=False).st_size > RECORD_BYTES:
            raise Refused(f"{path} is larger than any job record")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(RECORD_BYTES + 1)
    except OSError as exc:
        raise Refused(f"cannot read {path}: {exc}") from None
    if len(data) > RECORD_BYTES:
        raise Refused(f"{path} is larger than any job record")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Refused(f"cannot read {path}: {exc}") from None


def _inline(text: str) -> str:
    """``text`` kept to its one line inside one code span: a backtick reads as a quote, and every
    character that is not printable (a line break, a line separator, a control character) is
    written as its escape. A test id is the suite's text, and may hold any of them."""
    return "".join("'" if ch == "`" else ch if ch.isprintable() else ascii(ch)[1:-1] for ch in text)


#: The most bytes the step summary lists failed tests in. GitHub refuses a step summary over
#: 1 MiB whole, the verdict's reasons with it, so a mass failure's summary lists the tests within
#: this and says how many more there are; the log lists every one.
SUMMARY_TESTS_BYTES = 512 * 1024


def tests_summary(tests: Sequence[FailedTest], *, budget: int | None = None) -> str:
    """Each failed test as Markdown, one line each, after a line saying what they are; empty
    when there are none. With a ``budget``, the tests' lines stop within that many bytes, and a
    last line says how many more there are."""
    if not tests:
        return ""
    lines = [
        "",
        "Each failed test the records name, and how it stands. A flake is still a failure: only a"
        " quarantine entry waives one.",
        "",
    ]
    used = 0
    for n, t in enumerate(tests):
        why = f" ({_inline(t.why)})" if t.why else ""
        line = f"- `{_inline(t.key)}` attempt {t.attempt}: `{_inline(t.test)}`: {t.standing}{why}"
        used += len(line.encode("utf-8", "backslashreplace")) + 1
        if budget is not None and used > budget:
            lines.append(f"- {len(tests) - n} more failed tests: the log lists every one.")
            break
        lines.append(line)
    return "\n".join(lines) + "\n"


def summary(verdict: Verdict, *, budget: int | None = None) -> str:
    """The verdict as Markdown, one line per reason, then each failed test the records name
    (within ``budget`` bytes, when given: :func:`tests_summary`)."""
    if verdict.green:
        head = "### Verdict: green\n\nEvery planned job passed on this candidate.\n"
        return head + tests_summary(verdict.tests, budget=budget)
    lines = ["### Verdict: red", ""]
    for r in verdict.reasons:
        name = f"`{r.key}`" if r.key else "the plan"
        lines.append(f"- {name}: {r.kind}" + (f" ({r.detail})" if r.detail else ""))
    return "\n".join(lines) + "\n" + tests_summary(verdict.tests, budget=budget)


# ── the command ───────────────────────────────────────────────────────────────────────────────


class _Parser(argparse.ArgumentParser):
    # A usage error is unreadable input (exit 2) that reaches the summary, not a SystemExit that
    # escapes ``python -m tools.ci``'s dispatcher. Built with add_help=False, so no action exits:
    # a help page would exit 0, the green code, having judged nothing.
    def error(self, message: str) -> NoReturn:
        raise Refused(message)


class _Once(argparse.Action):
    """A single-valued option. Given twice, it is refused rather than the last one winning."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[object] | None,
        option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, None) is not None:
            raise argparse.ArgumentError(self, "given twice")
        setattr(namespace, self.dest, values)


def _partners(pairs: Sequence[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs:
        name, sep, sha = pair.partition("=")
        if not sep:
            raise Refused(f"a partner is NAME=SHA, got {pair!r}")
        if name in out:
            raise Refused(f"partner {name} is named twice")
        out[name] = sha
    return out


def _plan(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except RecursionError:
        raise Refused(f"cannot read the plan {path}: it is nested too deeply") from None
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot read the plan {path}: {exc}") from None
    if not isinstance(data, list) or not all(isinstance(k, str) and k for k in data):
        raise Refused(f"the plan {path} must be a JSON list of non-empty job keys")
    return data


def _json_file(path: str, what: str) -> object:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except RecursionError:
        raise Refused(f"{what} {path} is nested too deeply to read") from None
    except ValueError as exc:
        raise Refused(f"{what} is not JSON: {exc}") from None
    except OSError as exc:
        raise Refused(f"cannot read {what} {path}: {exc}") from None


def _attempt(text: str | None) -> int:
    if text is None:
        return 1
    if not ATTEMPT_TEXT.fullmatch(text):
        raise Refused(f"--attempt must be a whole number from 1 to 999999, got {text!r}")
    return int(text)


def _run_id(text: str | None) -> int:
    """``--run-id``: the run whose records ``--records`` holds."""
    if text is None:
        raise Refused("--records needs --run-id, the run whose records it holds")
    if not RUN_ID_TEXT.fullmatch(text):
        raise Refused(f"--run-id must be a whole number from 1, of 19 digits at most, got {text!r}")
    return int(text)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot read {path}: {exc}") from None


def _printable(text: str) -> str:
    # JSON can spell a lone surrogate in a job key, which no UTF-8 output can hold as it is.
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _to_summary(text: str) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        prog="verdict",
        description="One verdict over a plan's jobs.",
        add_help=False,
        allow_abbrev=False,
    )
    parser.add_argument("--plan", action=_Once, required=True, help="a JSON list of job keys")
    parser.add_argument("--needs", action=_Once, required=True, help="a file of toJSON(needs)")
    parser.add_argument("--head", action=_Once, required=True, help="the candidate's head SHA")
    parser.add_argument("--partner", action="append", default=[], help="the candidate's NAME=SHA")
    parser.add_argument("--run-head", action=_Once, required=True, help="this run's head SHA")
    parser.add_argument("--run-partner", action="append", default=[], help="the run's NAME=SHA")
    parser.add_argument(
        "--records", action=_Once, help="a folder of the per-job records the run uploaded"
    )
    parser.add_argument(
        "--run-id", action=_Once, help="this run's id, github.run_id (with --records only)"
    )
    parser.add_argument(
        "--key-partners",
        action=_Once,
        help="a JSON object of each planned key's partner names (with --records only)",
    )
    parser.add_argument(
        "--attempt", action=_Once, help="this run's attempt, 1 to 999999 (default 1)"
    )
    try:
        args = parser.parse_args(argv)
        candidate = Identity(args.head, _partners(args.partner))
        run = Identity(args.run_head, _partners(args.run_partner))
        attempt = _attempt(args.attempt)
        planned = _plan(args.plan)
        run_id: int | None = None
        key_partners: dict[str, Iterable[str]] | None = None
        if args.records is None:
            if args.run_id is not None:
                raise Refused("--run-id is read only with --records")
            if args.key_partners is not None:
                raise Refused("--key-partners is read only with --records")
            results = from_needs(_read(args.needs), run, attempt)
        else:
            run_id = own = _run_id(args.run_id)
            results = from_needs(_read(args.needs), run, attempt)
            # A planned key is met only by its record: a needs job of the same name would stand
            # in for a record that was never written.
            clash = sorted({r.key for r in results} & set(planned))
            if clash:
                raise Refused(
                    f"needs names {', '.join(clash)}, which"
                    f" {'is a planned key' if len(clash) == 1 else 'are planned keys'}: with"
                    " --records a planned key is met only by its record"
                )
            results += read_records(args.records, run_id=own, attempt=attempt)
            if args.key_partners is not None:
                by_key = _json_file(args.key_partners, "--key-partners")
                if not isinstance(by_key, dict):
                    raise Refused(
                        "--key-partners must be a mapping of each planned key to its partners"
                    )
                key_partners = by_key
        verdict = decide(planned, results, candidate, run_id=run_id, key_partners=key_partners)
    except Refused as exc:
        reason = _printable(str(exc))
        print(f"verdict: unreadable input: {reason}", file=sys.stderr)
        _to_summary(f"### Verdict: unreadable input\n\n{reason}\n")
        return 2
    print(_printable(summary(verdict)), end="")
    _to_summary(_printable(summary(verdict, budget=SUMMARY_TESTS_BYTES)))
    return 0 if verdict.green else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
