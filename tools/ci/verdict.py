"""One verdict over a plan's jobs: green only when every planned job did its work on this candidate.

:func:`decide` reads a plan (the job keys that had to run), the results it has, and the candidate
they must be for. The verdict is green only when all of these hold:

- the plan is not empty;
- every planned key has a result;
- every result for that key, on every attempt, is ``success``;
- every one of those results names the candidate's head and the candidate's partner revisions
  (:class:`Identity`), so a result for an older head, or an older head of a partner repository,
  is not this candidate's.

Anything else is red, with one :class:`Reason` per key that fell short: ``missing``,
``cancelled``, ``skipped``, ``failure``, ``unknown result``, ``no identity``, ``stale head``,
``stale partner``, or ``red on an earlier attempt`` (a red is never rerun into green). A job
outside the plan that was skipped or passed changes nothing; one that failed, was cancelled or
reported a word this module does not know, on any attempt, reads red, so the verdict fails closed.

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
appended to it.

:func:`job_record` writes, and :func:`from_job_records` reads, the per-job record a local run of a
catalogue job leaves (``python -m tools.ci run-job``): its key, result, identity, attempt, the
diagnostic rerun of a red job's failed tests (each classified :data:`FLAKE` or
:data:`REPEATED_FAILURE`, never turning the result green), which quarantined tests failed, and
each step's argv, exit and seconds.

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
class JobResult:
    """One job's result on one attempt. ``identity`` is None when the result named none."""

    key: str
    result: str
    identity: Identity | None
    attempt: int = 1


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


def _fault(result: JobResult, candidate: Identity) -> tuple[str, str] | None:
    """What is wrong with one planned result, or None when it counts for the candidate."""
    if result.result not in WORDS:
        return "unknown result", f"GitHub gives no job the result {result.result!r}"
    if result.result != SUCCESS:
        return result.result, f"attempt {result.attempt}"
    if result.identity is None:
        return "no identity", "the result names no head, so it cannot be this candidate's"
    if result.identity.head_sha != candidate.head_sha:
        return "stale head", f"ran on {result.identity.head_sha}, not {candidate.head_sha}"
    if dict(result.identity.external_revisions) != dict(candidate.external_revisions):
        return "stale partner", (
            f"ran with partners {_revisions(result.identity)}, not {_revisions(candidate)}"
        )
    return None


def _revisions(identity: Identity) -> str:
    pairs = sorted(identity.external_revisions.items())
    return "{" + ", ".join(f"{k}={v}" for k, v in pairs) + "}" if pairs else "none"


def _planned(results: Sequence[JobResult], candidate: Identity) -> tuple[str, str] | None:
    """The fault of one planned key's results, or None when every attempt was green here."""
    if not results:
        return "missing", "no result for a planned job"
    latest = max(r.attempt for r in results)
    for r in results:
        if r.attempt == latest:
            fault = _fault(r, candidate)
            if fault:
                return fault
    for r in sorted(results, key=lambda r: r.attempt):
        fault = _fault(r, candidate)
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


def decide(planned: Iterable[str], results: Iterable[JobResult], candidate: Identity) -> Verdict:
    """The verdict on ``candidate``: green only when every planned job did its work on it."""
    plan: list[str] = []
    for key in planned:
        if not isinstance(key, str) or not key:
            raise Refused(f"a planned job key must be a non-empty string, got {key!r}")
        if key not in plan:
            plan.append(key)
    by_key: dict[str, list[JobResult]] = {}
    for r in results:
        by_key.setdefault(r.key, []).append(r)
    reasons: list[Reason] = []
    if not plan:
        reasons.append(Reason("", "empty plan", "a plan with no jobs proves nothing"))
    for key in plan:
        fault = _planned(by_key.get(key, []), candidate)
        if fault:
            reasons.append(Reason(key, *fault))
    for key in sorted(set(by_key) - set(plan)):
        fault = _unplanned(by_key[key])
        if fault:
            reasons.append(Reason(key, *fault))
    return Verdict(green=not reasons, reasons=tuple(reasons))


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
    return Verdict(green=False, reasons=reasons, superseded_by=by.pop() if whole else "")


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
    """The results in GitHub's ``toJSON(needs)``, each stamped with the run's ``identity``.

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
JOB_RECORD_SCHEMA = "personalclaw.ci.job-record/1"
_RECORD_KEYS = frozenset(
    {
        "schema",
        "key",
        "result",
        "head_sha",
        "external_revisions",
        "attempt",
        "rerun",
        "quarantined",
        "plan",
        "workers",
        "steps",
    }
)
_STEP_RECORD_KEYS = frozenset({"argv", "exit", "seconds"})
_RERUN_KEYS = frozenset({"argv", "exit", "tests"})
_RERUN_TEST_KEYS = frozenset({"test", "rerun_result", "why"})

#: How the diagnostic rerun classifies a failed test: it passed alone in a fresh process, or it
#: failed again. A test that was not rerun (a rail, a security-marked test) is classified null.
#: Neither word turns a red green: the first result is the result.
FLAKE = "flake"
REPEATED_FAILURE = "repeated failure"
RERUN_RESULTS = frozenset({FLAKE, REPEATED_FAILURE})
#: Where a sharded job's file list came from: CI's own plan, or a local run's even split.
PLANS = frozenset({"ci", "offline"})


def job_record(
    *,
    key: str,
    result: str,
    identity: Identity,
    steps: Sequence[tuple[Sequence[str], int, float]],
    attempt: int = 1,
    rerun: Mapping[str, object] | None = None,
    quarantined: Sequence[str] = (),
    plan: str | None = None,
    workers: int | None = None,
) -> str:
    """One job's (or one shard's) result as the JSON :func:`from_job_records` reads.

    ``steps`` are each step's argv as run, its exit status and its seconds. ``rerun`` is the
    diagnostic rerun of a red job's failed tests, or None when there was no failed test to
    diagnose: the rerun's ``argv`` and ``exit`` (both null when no failed test could be rerun),
    and each failed test with its ``rerun_result`` (:data:`FLAKE`, :data:`REPEATED_FAILURE`, or
    null when it was not rerun) and ``why``. Only a failure carries one, and it never changes
    ``result``. ``quarantined`` names the quarantined tests that failed, for a reader that keeps
    only a clean pass. ``plan`` is where a sharded job's list came from (one of :data:`PLANS`),
    ``workers`` a worker count a local run set in place of the catalogue's.
    """
    record: dict[str, object] = {
        "schema": JOB_RECORD_SCHEMA,
        "key": key,
        "result": result,
        "head_sha": identity.head_sha,
        "external_revisions": dict(sorted(identity.external_revisions.items())),
        "attempt": attempt,
        # A copy of a mapping; anything else is written as given, so the reader refuses it by name.
        "rerun": dict(rerun) if isinstance(rerun, Mapping) else rerun,
        "quarantined": list(quarantined),
        "plan": plan,
        "workers": workers,
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
    if set(data) != _RECORD_KEYS:
        raise Refused(f"{where} must hold exactly {sorted(_RECORD_KEYS)}, got {sorted(data)}")
    if data["schema"] != JOB_RECORD_SCHEMA:
        raise Refused(f"{where} has schema {data['schema']!r}, not {JOB_RECORD_SCHEMA!r}")
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
    return JobResult(key, data["result"], identity, data["attempt"])


def from_job_records(texts: Iterable[str]) -> list[JobResult]:
    """The results in the per-job records :func:`job_record` writes, one text per record.

    Like :func:`from_needs` it refuses what it cannot read as a record: a repeated key, NaN or
    Infinity, a field missing or unknown, a result word that is no job's, an object id that is
    not a full one, a step's seconds that are not a finite number, a success with a step that
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


def summary(verdict: Verdict) -> str:
    """The verdict as Markdown, one line per reason."""
    if verdict.green:
        return "### Verdict: green\n\nEvery planned job passed on this candidate.\n"
    lines = ["### Verdict: red", ""]
    for r in verdict.reasons:
        name = f"`{r.key}`" if r.key else "the plan"
        lines.append(f"- {name}: {r.kind}" + (f" ({r.detail})" if r.detail else ""))
    return "\n".join(lines) + "\n"


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
    try:
        args = parser.parse_args(argv)
        candidate = Identity(args.head, _partners(args.partner))
        run = Identity(args.run_head, _partners(args.run_partner))
        planned = _plan(args.plan)
        results = from_needs(_read(args.needs), run)
    except Refused as exc:
        reason = _printable(str(exc))
        print(f"verdict: unreadable input: {reason}", file=sys.stderr)
        _to_summary(f"### Verdict: unreadable input\n\n{reason}\n")
        return 2
    verdict = decide(planned, results, candidate)
    text = _printable(summary(verdict))
    print(text, end="")
    _to_summary(text)
    return 0 if verdict.green else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
