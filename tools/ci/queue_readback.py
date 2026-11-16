"""``python -m tools.ci queue-readback``: main's merge queue settings, read back (0.4.0 A1, P5).

P5 is one ruleset change: ``main-protect`` gains a ``merge_queue`` rule and drops strict
required checks, and ``MERGE_QUEUE_LIVE`` is set to ``true`` in the same step. This reads what
GitHub then holds and prints one line a condition, so the change is judged by what took effect
and not by the body that was sent. It sends GETs only, through ``gh api``, and changes nothing.

**The gates**, each ``PASS`` or ``FAIL``:

- **A merge queue:** exactly one ``merge_queue`` rule among main's rules.
- **ALLGREEN:** each entry's own queue commit must be green, so every commit a push lands is a
  tree a verdict judged.
- **SQUASH:** a merge-method queue commit has two parents, so its verdict is always red.
- **The queue's sizes:** :data:`QUEUE_SIZES`, the design's (3 built at once, 1 to 5 merged, a
  5-minute wait, a 60-minute check timeout).
- **No bypass actor** on any ruleset whose rules apply to main, each read whole. GitHub shows a
  ruleset's ``bypass_actors`` only to a caller who may edit it, so an answer without them is
  ``not read``, never a pass, and a ``FAIL`` names only how many there are and their types.
- **merge-verdict on queue commits:** ``merge-verdict`` is a required check of main and main
  queues its merges, so GitHub holds every queue commit to it.
- **Pinned to the controller App:** every required ``merge-verdict`` names the integration id of
  :data:`CONTROLLER_SLUG`, the App the poster mints its token for, and the ``ci-controller``
  environment's ``CONTROLLER_APP_ID`` names that same App.
- **Strict off:** no required-checks rule for main asks for a head up to date with main.
- **MERGE_QUEUE_LIVE:** the repository variable reads ``true``.
- **The poster answers a queue commit** (proof 2): a queue commit whose ``merge_group`` run of
  merge.yml completed at least :data:`SETTLE_MINUTES` ago carries a ``merge-verdict`` check
  from the controller App. Until such a run exists it is ``WAIT``, not a pass.

**A fact, not a gate:** whether CodeQL's default setup ran on a queue commit (proof 6, which
decides C3). It prints ``INFO`` with the answer, or ``WAIT`` before the first settled queue run.

A read that fails is that line's ``FAIL`` (``not read: …``). Exit 0 when every gate passes, 1
when any fails, and 3 when none fails but one still waits for the first queue run. Standard
library only, like every file in ``tools/ci``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from . import workflows
from .merge_controller import CODEQL_WORKFLOW, QUEUE_BRANCH

REPO = "PersonalClaw/PersonalClaw"
#: The controller App: it posts ``merge-verdict`` with a token minted for it.
CONTROLLER_SLUG = "personalclaw-ci-controller"
#: The environment whose ``CONTROLLER_APP_ID`` the poster mints that token with.
CONTROLLER_ENVIRONMENT = "ci-controller"
VARIABLE = "MERGE_QUEUE_LIVE"
CHECK = "merge-verdict"
MERGE_PATH = f".github/workflows/{workflows.MERGE}"
#: The queue's sizes, as the design sets them.
QUEUE_SIZES: Mapping[str, int] = {
    "max_entries_to_build": 3,
    "max_entries_to_merge": 5,
    "min_entries_to_merge": 1,
    "min_entries_to_merge_wait_minutes": 5,
    "check_response_timeout_minutes": 60,
}
#: A queue run completed this long ago has had its verdict posted, if one comes.
SETTLE_MINUTES = 15
#: The newest settled queue runs read for proofs 2 and 6.
QUEUE_RUNS_READ = 5
GH_TIMEOUT_SECONDS = 60
_STATUS = re.compile(r"\(HTTP (\d{3})\)")

PASS, FAIL, WAIT, INFO = "PASS", "FAIL", "WAIT", "INFO"
POSTER = "the poster answers a queue commit"
CODEQL = "CodeQL on queue commits (proof 6, a fact for C3)"


class Unread(Exception):
    """An answer the API did not give, with its HTTP status when it named one."""

    def __init__(self, what: str, status: int | None = None) -> None:
        super().__init__(what)
        self.status = status


#: ``get(path)`` returns the decoded JSON body of ``GET path``, or raises :class:`Unread`.
Get = Callable[[str], Any]


@dataclass(frozen=True, slots=True)
class Line:
    state: str
    name: str
    detail: str

    def text(self) -> str:
        return f"{self.state} {self.name}: {self.detail}"


def gh_get(path: str) -> Any:
    """``GET path`` through ``gh api``, which holds its own credential."""
    try:
        done = subprocess.run(
            ["gh", "api", path],
            capture_output=True,
            text=True,
            errors="replace",
            check=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as exc:
        found = _STATUS.search(exc.stderr or "")
        tail = (exc.stderr or "").strip().splitlines()[-1:] or ["no answer"]
        raise Unread(f"GET {path}: {tail[0]}", int(found.group(1)) if found else None) from None
    except (subprocess.SubprocessError, OSError) as exc:
        raise Unread(f"GET {path}: {exc}") from None
    try:
        return json.loads(done.stdout)
    except ValueError:
        raise Unread(f"GET {path}: the answer is not JSON") from None


def _list(value: object, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise Unread(f"{what} is not a list")
    return value


def _map(value: object, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Unread(f"{what} is not an object")
    return value


@dataclass(frozen=True, slots=True)
class _Main:
    """Main's rules, as one read."""

    rules: list[Mapping[str, Any]]

    def of(self, kind: str) -> list[Mapping[str, Any]]:
        return [r for r in self.rules if r.get("type") == kind]

    def parameters(self, rule: Mapping[str, Any]) -> Mapping[str, Any]:
        return _map(rule.get("parameters"), f"main's {rule.get('type')} rule's parameters")


def _rules(get: Get, repo: str) -> _Main:
    found = _list(get(f"repos/{repo}/rules/branches/main?per_page=100"), "main's rules")
    return _Main([_map(r, "a rule of main") for r in found])


def _queue(main: _Main) -> Mapping[str, Any]:
    queues = main.of("merge_queue")
    if len(queues) != 1:
        raise _No(f"main's rules hold {len(queues)} merge_queue rules, not one")
    return main.parameters(queues[0])


class _No(Exception):
    """A gate that is read, and does not hold."""


def _queue_line(main: _Main) -> str:
    _queue(main)
    return "main's rules hold one merge_queue rule"


def _grouping(main: _Main) -> str:
    got = _queue(main).get("grouping_strategy")
    if got != "ALLGREEN":
        raise _No(f"grouping_strategy is {got!r}, not 'ALLGREEN'")
    return "grouping_strategy is ALLGREEN: every entry's own queue commit must be green"


def _method(main: _Main) -> str:
    got = _queue(main).get("merge_method")
    if got != "SQUASH":
        raise _No(f"merge_method is {got!r}, not 'SQUASH'")
    return "merge_method is SQUASH: each queue commit has one parent"


def _sizes(main: _Main) -> str:
    params = _queue(main)
    wrong = [
        f"{k} is {params.get(k)!r}, not {v}" for k, v in QUEUE_SIZES.items() if params.get(k) != v
    ]
    if wrong:
        raise _No("; ".join(wrong))
    return ", ".join(f"{k} {v}" for k, v in QUEUE_SIZES.items())


def _bypass(get: Get, repo: str, main: _Main) -> str:
    ids = sorted({r["ruleset_id"] for r in main.rules if isinstance(r.get("ruleset_id"), int)})
    if not ids:
        raise _No("no rule of main names its ruleset")
    named: list[str] = []
    for ident in ids:
        ruleset = _map(get(f"repos/{repo}/rulesets/{ident}"), f"ruleset {ident}")
        if "bypass_actors" not in ruleset:
            raise Unread(
                f"ruleset {ident} ({ruleset.get('name')}) answered without its bypass_actors, "
                "which GitHub shows only to a caller who may edit it"
            )
        actors = _list(ruleset["bypass_actors"], f"ruleset {ident}'s bypass_actors")
        if actors:
            # How many and of which types only: the output goes into a public pull request,
            # and GitHub shows the list itself only to a caller who may edit the ruleset.
            kinds = sorted(
                {
                    str(a.get("actor_type") or "unnamed") if isinstance(a, Mapping) else "unread"
                    for a in actors
                }
            )
            raise _No(
                f"ruleset {ident} ({ruleset.get('name')}) has {len(actors)} bypass actor(s), of"
                f" type {', '.join(kinds)}"
            )
        named.append(f"{ident} ({ruleset.get('name')})")
    return f"no bypass actor on {', '.join(named)}"


def _required(main: _Main) -> list[Mapping[str, Any]]:
    """Every required ``merge-verdict`` entry of main's required-checks rules."""
    found: list[Mapping[str, Any]] = []
    for rule in main.of("required_status_checks"):
        checks = _list(main.parameters(rule).get("required_status_checks"), "the required checks")
        found += [
            _map(c, "a required check")
            for c in checks
            if isinstance(c, Mapping) and c.get("context") == CHECK
        ]
    return found


def _on_queue_commits(main: _Main) -> str:
    if not _required(main):
        raise _No(f"{CHECK} is not a required check of main")
    _queue(main)
    return f"{CHECK} is required, and main queues its merges, so every queue commit is held to it"


def _app_id(get: Get) -> int:
    ident = _map(get(f"apps/{CONTROLLER_SLUG}"), f"the App {CONTROLLER_SLUG}").get("id")
    if not isinstance(ident, int) or isinstance(ident, bool):
        raise Unread(f"the App {CONTROLLER_SLUG} names no integer id")
    return ident


def _pinned(get: Get, repo: str, main: _Main) -> str:
    required = _required(main)
    if not required:
        raise _No(f"{CHECK} is not a required check of main")
    app = _app_id(get)
    pins = sorted({str(c.get("integration_id")) for c in required})
    if pins != [str(app)]:
        raise _No(f"{CHECK} is pinned to {', '.join(pins)}, not {CONTROLLER_SLUG}'s {app}")
    path = f"repos/{repo}/environments/{CONTROLLER_ENVIRONMENT}/variables/CONTROLLER_APP_ID"
    minted = _map(get(path), "CONTROLLER_APP_ID").get("value")
    if str(minted).strip() != str(app):
        raise _No(f"{CONTROLLER_ENVIRONMENT}'s CONTROLLER_APP_ID is {minted!r}, not {app}")
    return (
        f"pinned to {CONTROLLER_SLUG} ({app}), the App {CONTROLLER_ENVIRONMENT} mints the"
        " poster's token for"
    )


def _strict(main: _Main) -> str:
    rules = main.of("required_status_checks")
    if not rules:
        raise _No("main has no required-checks rule")
    strict = [main.parameters(r).get("strict_required_status_checks_policy") for r in rules]
    if any(s is not False for s in strict):
        raise _No(f"strict_required_status_checks_policy reads {strict}, not all false")
    return "no required-checks rule asks for a head up to date with main"


def _variable(get: Get, repo: str) -> str:
    try:
        value = _map(get(f"repos/{repo}/actions/variables/{VARIABLE}"), VARIABLE).get("value")
    except Unread as exc:
        if exc.status == 404:
            raise _No(f"{VARIABLE} is not set") from None
        raise
    if value != "true":
        raise _No(f"{VARIABLE} is {value!r}, not 'true'")
    return f"{VARIABLE} is 'true'"


def _at(value: object) -> datetime.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def settled_queue_runs(
    get: Get, repo: str, now: datetime.datetime
) -> tuple[list[Mapping[str, Any]], int]:
    """The newest completed ``merge_group`` runs of merge.yml on main's queue that finished at
    least :data:`SETTLE_MINUTES` ago, and how many completed runs there were in all."""
    page = _map(
        get(
            f"repos/{repo}/actions/workflows/{workflows.MERGE}/runs"
            "?event=merge_group&status=completed&per_page=100"
        ),
        "the queue runs",
    )
    runs = [
        r
        for r in _list(page.get("workflow_runs"), "the queue runs")
        if isinstance(r, Mapping)
        and r.get("event") == "merge_group"
        and r.get("path") == MERGE_PATH
        and QUEUE_BRANCH.fullmatch(str(r.get("head_branch") or ""))
        and isinstance(r.get("head_sha"), str)
    ]
    cut = now - datetime.timedelta(minutes=SETTLE_MINUTES)
    settled = [r for r in runs if (at := _at(r.get("updated_at"))) is not None and at <= cut]
    settled.sort(key=lambda r: str(r.get("updated_at")), reverse=True)
    return settled[:QUEUE_RUNS_READ], len(runs)


def _waiting(total: int) -> str:
    if not total:
        return "no merge_group run of merge.yml has completed on main's queue yet"
    return f"{total} queue run(s) completed, none {SETTLE_MINUTES} minutes ago yet: read again"


def _poster(get: Get, repo: str, runs: Sequence[Mapping[str, Any]], total: int) -> Line:
    if not runs:
        return Line(WAIT, POSTER, _waiting(total))
    app = _app_id(get)
    for run in runs:
        sha = str(run["head_sha"])
        checks = _map(
            get(f"repos/{repo}/commits/{sha}/check-runs?check_name={CHECK}&per_page=100"),
            f"{sha[:12]}'s check runs",
        )
        ours = [
            c
            for c in _list(checks.get("check_runs"), f"{sha[:12]}'s check runs")
            if isinstance(c, Mapping)
            and c.get("name") == CHECK
            and _map(c.get("app") or {}, "a check run's App").get("id") == app
            and c.get("status") == "completed"
        ]
        if ours:
            return Line(
                PASS,
                POSTER,
                f"queue commit {sha[:12]} (merge.yml run {run.get('id')}) carries {CHECK} from "
                f"{CONTROLLER_SLUG}: {ours[0].get('conclusion')}",
            )
    shas = ", ".join(str(r["head_sha"])[:12] for r in runs)
    return Line(
        FAIL, POSTER, f"no completed {CHECK} from {CONTROLLER_SLUG} on queue commit(s) {shas}"
    )


def _codeql(get: Get, repo: str, runs: Sequence[Mapping[str, Any]], total: int) -> Line:
    if not runs:
        return Line(WAIT, CODEQL, _waiting(total))
    for run in runs:
        sha = str(run["head_sha"])
        theirs = _map(
            get(f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100"), f"{sha[:12]}'s runs"
        )
        found = [
            r
            for r in _list(theirs.get("workflow_runs"), f"{sha[:12]}'s runs")
            if isinstance(r, Mapping) and str(r.get("path") or "").startswith(CODEQL_WORKFLOW)
        ]
        if found:
            return Line(
                INFO,
                CODEQL,
                f"yes: default setup's run {found[0].get('id')} ({found[0].get('event')}) on queue "
                f"commit {sha[:12]}, {found[0].get('conclusion') or found[0].get('status')}",
            )
    shas = ", ".join(str(r["head_sha"])[:12] for r in runs)
    return Line(INFO, CODEQL, f"no: default setup ran on none of queue commit(s) {shas}")


def readback(get: Get, repo: str, *, now: datetime.datetime) -> list[Line]:
    """One line a condition, in the order the module's docstring gives."""
    lines: list[Line] = []
    try:
        main: _Main | Unread = _rules(get, repo)
    except Unread as exc:
        main = exc

    def gate(name: str, check: Callable[[_Main], str]) -> None:
        if isinstance(main, Unread):
            lines.append(Line(FAIL, name, f"not read: {main}"))
            return
        try:
            lines.append(Line(PASS, name, check(main)))
        except _No as no:
            lines.append(Line(FAIL, name, str(no)))
        except Unread as exc:
            lines.append(Line(FAIL, name, f"not read: {exc}"))

    gate("a merge queue on main", _queue_line)
    gate("ALLGREEN", _grouping)
    gate("SQUASH", _method)
    gate("the queue's sizes", _sizes)
    gate("no bypass actor", lambda m: _bypass(get, repo, m))
    gate(f"{CHECK} required on queue commits", _on_queue_commits)
    gate(f"{CHECK} pinned to the controller App", lambda m: _pinned(get, repo, m))
    gate("strict off", _strict)
    gate(VARIABLE, lambda _m: _variable(get, repo))
    # Proofs 2 and 6 read the queue's own runs: a failed read is the poster's FAIL, and leaves
    # proof 6, a fact, unread.
    try:
        runs, total = settled_queue_runs(get, repo, now)
        lines.append(_poster(get, repo, runs, total))
    except Unread as exc:
        return [
            *lines,
            Line(FAIL, POSTER, f"not read: {exc}"),
            Line(INFO, CODEQL, f"not read: {exc}"),
        ]
    try:
        lines.append(_codeql(get, repo, runs, total))
    except Unread as exc:
        lines.append(Line(INFO, CODEQL, f"not read: {exc}"))
    return lines


def status(lines: Sequence[Line]) -> int:
    """0 when every gate passes, 1 when any fails, 3 when none fails and one still waits."""
    states = {line.state for line in lines}
    if FAIL in states:
        return 1
    return 3 if WAIT in states else 0


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci queue-readback",
        description=(__doc__ or "").splitlines()[0],
    )
    parser.add_argument("--repo", default=REPO)
    args = parser.parse_args(argv)
    lines = readback(gh_get, args.repo, now=_now())
    for line in lines:
        print(line.text())
    counts = {s: sum(1 for line in lines if line.state == s) for s in (PASS, FAIL, WAIT, INFO)}
    print("queue-readback: " + ", ".join(f"{n} {s.lower()}" for s, n in counts.items()))
    return status(lines)
