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

Standard library only, with no relative imports, so a trusted job can run it as a script.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
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


def _no_repeats(pairs: list[tuple[str, object]]) -> dict[str, object]:
    # JSON would keep a repeated key's last value, so a job's failure could be followed by its
    # success. GitHub never writes a key twice.
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise Refused(f"needs repeats the key {key!r}")
        out[key] = value
    return out


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
