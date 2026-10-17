"""The breadth run's evidence record: what each leg ran on, what it found, and under which digest.

A breadth leg (a platform and Python, run nightly instead of per push) records the exact inputs
it ran on: the tree, the platform image and its version, the architecture, the toolchain, and
any external input it read. One canonical JSON document holds every leg of a run, and it is
named by the sha256 of its bytes (``breadth-evidence-<digest>``). Whatever relies on a record
names it by run id and digest, and a reader refuses a record whose bytes do not hash to that
digest (:func:`verify`). The release gate reads records, never a run summary.

A tree-keyed leg reuses an earlier result only when every recorded input matches exactly
(:func:`reusable`), and then carries that result forward, red or green, naming the record it
came from (:func:`carried`). A red is never run again on the same inputs to turn it green. A leg
that may not be reused (security, advisory, live or time-varying) runs every night. A run whose
legs were reused does not satisfy a release on its own (:func:`satisfies_release_alone`).

``report`` keeps one issue per failing leg, under an exact title (:func:`issue_title`), and
:func:`settle` says what each leg's result does to that issue: a red opens or updates it; a
green on changed inputs closes it; a green on the inputs the red ran on is a flake, which keeps
the issue open and the leg red in the record.

Standard library only, like every file in ``tools/ci``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

VERSION = 1
PREFIX = "breadth-evidence-"
GREEN = "success"
RED = "failure"

#: Every input a tree-keyed leg's reuse is keyed on. A leg missing one is never reused.
INPUT_KEYS = ("tree", "image", "image_version", "arch", "python", "uv", "node", "external")

#: Legs that may not be reused: security, advisory, live or time-varying. They run every night,
#: whatever the tree. The contract row holds ``breadth.yml``'s jobs to this list.
NON_REUSABLE = frozenset({"security-corpus", "canary", "install-smoke", "audit"})

#: Every leg ``breadth.yml`` runs, and how many shards it has. A run's record holds an entry for
#: each, and a shard that wrote none is recorded red (:func:`fill_missing`). The contract row
#: holds ``breadth.yml``'s jobs and matrices to this map.
SHARDS: Mapping[str, int] = {
    "ubuntu-24.04-3.13": 4,
    "macos-26-3.12": 6,
    "ubuntu-24.04-arm-3.13": 4,
    "coverage": 6,
    "security-corpus": 1,
    "canary": 4,
    "install-smoke": 1,
    "audit": 1,
}


def expected_keys() -> frozenset[str]:
    """Every ``<leg>/<shard>`` key a run's record must hold."""
    return frozenset(f"{leg}/{n}" for leg, count in SHARDS.items() for n in range(1, count + 1))


def fill_missing(entries: Mapping[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """``entries`` with a red entry for every expected shard that wrote none.

    A shard whose runner was lost, or which failed before it recorded anything, ran nothing a
    record can rely on: it is red, never absent. Its empty inputs never match, so it runs the
    next night instead of being carried.
    """
    filled = {key: dict(entry) for key, entry in entries.items()}
    for key in sorted(expected_keys() - set(filled)):
        filled[key] = {"inputs": {}, "result": RED, "missing": True}
    return filled


class Refused(ValueError):
    """A record that cannot be relied on: wrong digest, wrong shape, or wrong version."""


def canonical(record: Mapping[str, Any]) -> bytes:
    """The record's one byte form: sorted keys, no spaces, UTF-8."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact_name(record_digest: str) -> str:
    return PREFIX + record_digest


def verify(data: bytes, record_digest: str) -> dict[str, Any]:
    """The record ``data`` holds, if and only if it hashes to ``record_digest`` and is a record."""
    if digest(data) != record_digest:
        raise Refused(f"the record does not hash to {record_digest}")
    try:
        record = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Refused(f"the record is not JSON: {exc}") from exc
    if not isinstance(record, dict) or record.get("version") != VERSION:
        raise Refused(f"the record is not a version {VERSION} breadth record")
    legs = record.get("legs")
    if not isinstance(legs, dict) or not legs:
        raise Refused("the record names no legs")
    for leg_id, leg in legs.items():
        if not isinstance(leg, dict) or leg.get("result") not in (GREEN, RED):
            raise Refused(f"leg {leg_id!r} has no result")
        if not isinstance(leg.get("inputs"), dict):
            raise Refused(f"leg {leg_id!r} has no inputs")
    return record


#: The marks of an entry whose result measured nothing: a shard that wrote no entry, a green
#: whose issue could not be read, and a green off main that ran without its earlier results.
UNMEASURED = ("missing", "unsettled", "unread")


def measured(entry: Mapping[str, Any] | None) -> bool:
    """Whether ``entry`` holds a result its shard measured, which a later run may carry."""
    return isinstance(entry, Mapping) and not any(entry.get(mark) for mark in UNMEASURED)


def inputs_key(inputs: Mapping[str, Any]) -> str:
    """One digest for a leg's inputs, so an issue can say which inputs its red ran on."""
    return digest(canonical(inputs))


def reusable(leg_id: str, earlier: Mapping[str, Any] | None, now: Mapping[str, Any]) -> bool:
    """Whether ``leg_id`` may carry ``earlier``'s result instead of running on inputs ``now``.

    Only a tree-keyed leg, only a result that leg executed (not one it carried) and measured
    (:func:`measured`), and only when every input is present and equal. A missing input is a
    difference, never a match.
    """
    if leg_id in NON_REUSABLE or earlier is None or not measured(earlier):
        return False
    if earlier.get("reused_from"):
        return False
    then = earlier.get("inputs")
    if not isinstance(then, Mapping):
        return False
    return all(key in then and key in now and then[key] == now[key] for key in INPUT_KEYS)


def carried(earlier: Mapping[str, Any], run_id: int, record_digest: str) -> dict[str, Any]:
    """The leg entry of a reused leg: the earlier result, red or green, and where it came from."""
    leg = {k: v for k, v in earlier.items() if k != "reused_from"}
    leg["reused_from"] = {"run_id": run_id, "digest": record_digest}
    return leg


def satisfies_release_alone(record: Mapping[str, Any]) -> bool:
    """A record satisfies a release alone only when every expected shard ran in it, and every
    one is green. A record that lacks a shard does not.

    A reused leg's result belongs to the record it names, which the release gate follows.
    """
    legs = record.get("legs")
    if not isinstance(legs, Mapping) or not legs or not expected_keys() <= set(legs):
        return False
    return all(
        isinstance(leg, Mapping) and not leg.get("reused_from") and leg.get("result") == GREEN
        for leg in legs.values()
    )


def issue_title(leg_id: str) -> str:
    """The exact title ``report`` finds a leg's issue by. No label: one that does not exist
    makes creating the issue fail."""
    return f"breadth: {leg_id} is red"


@dataclass(frozen=True)
class Settled:
    """What one executed leg's result does: the result the record keeps, and the issue's fate."""

    recorded: str
    action: str  # "open" | "update" | "close" | "flake" | "none"


def settle(
    result: str, inputs: Mapping[str, Any], open_red_inputs: frozenset[str] | None
) -> Settled:
    """``open_red_inputs`` is every :func:`inputs_key` the open issue's reds ran on, or None.

    - red: open the issue, or update the open one with this run;
    - green with no open issue: nothing;
    - green on inputs that differ from every red's: close it;
    - green on any red's very inputs: a flake. The issue stays open with both runs, and the
      record keeps the leg red, until a fix (changed inputs) or a quarantine closes it. A later
      red (one that wrote nothing, or read nothing) never hides an earlier red's inputs.
    """
    if result not in (GREEN, RED):
        raise ValueError(f"not a result: {result!r}")
    if result == RED:
        return Settled(RED, "open" if open_red_inputs is None else "update")
    if open_red_inputs is None:
        return Settled(GREEN, "none")
    if inputs_key(inputs) in open_red_inputs:
        return Settled(RED, "flake")
    return Settled(GREEN, "close")


#: When a night begins, in UTC: the ``schedule`` slot ``breadth.yml``'s cron names. The
#: contract row holds the two to each other.
NIGHT_STARTS_UTC = (9, 37)


def night_of(created_at: str) -> str:
    """The night a run belongs to: the date of the scheduled slot at or before its creation.

    A run's ``created_at`` is the same on every attempt, so a rerun keeps its run's night, and a
    dispatch before the next scheduled slot shares the night of the run it follows.
    """
    when = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    hour, minute = NIGHT_STARTS_UTC
    return (when - timedelta(hours=hour, minutes=minute)).date().isoformat()


# ── what breadth.yml runs ──────────────────────────────────────────────────────────────────────
#
# A record's legs are keyed ``<leg>/<shard>`` (``macos-26-3.12/4``); a leg is a platform and
# Python, and its issue lists its red shards. ``freshness`` writes :func:`previous`'s map, each
# shard reads it with :func:`decide` against its own runner's inputs, and ``report`` settles the
# legs, writes the record and keeps the issues (:func:`settle_record`, :func:`apply`).

#: The line an issue carries for each red it records: the inputs that red ran on.
MARKER = "breadth-inputs: "


def leg_of(key: str) -> str:
    return key.split("/", 1)[0]


class Source(Protocol):
    """Where earlier records come from: completed ``breadth.yml`` runs on ``main``."""

    def runs(self) -> list[int]:
        """Their run ids, newest first."""
        ...

    def artifacts(self, run_id: int) -> list[str]: ...

    def record(self, run_id: int, name: str) -> bytes: ...


def _named(source: Source, run_id: int, record_digest: str) -> dict[str, Any]:
    name = artifact_name(record_digest)
    if name not in source.artifacts(run_id):
        raise Refused(f"run {run_id} has no {name}")
    return verify(source.record(run_id, name), record_digest)


def _executed(
    source: Source, key: str, entry: Mapping[str, Any], run_id: int, record_digest: str
) -> dict[str, Any] | None:
    """``entry``, or the entry it was carried from, with the run that executed it; None when
    a carried entry's origin cannot be read."""
    origin = entry.get("reused_from")
    if not origin:
        return {"entry": dict(entry), "run_id": run_id, "digest": record_digest}
    try:
        then = _named(source, int(origin["run_id"]), str(origin["digest"]))
    except (Refused, KeyError, TypeError, ValueError):
        return None
    executed = then["legs"].get(key)
    if not isinstance(executed, dict) or executed.get("reused_from"):
        return None
    return {"entry": executed, "run_id": int(origin["run_id"]), "digest": str(origin["digest"])}


def previous(source: Source, *, limit: int = 30) -> tuple[dict[str, Any], str]:
    """Per leg key, the newest MEASURED result for it, from the run that executed it.

    The newest record names the keys. A key it carried is followed once, to the record it
    names, verified under its own digest; a key whose origin cannot be read is left out, so its
    shard runs. A key whose newest entry measured nothing (:func:`measured`: a shard that wrote
    no entry, a green whose issue was unread, a green that ran blind) is looked for in older
    records, so a red is never hidden behind one: it is carried, not run again on its inputs.
    A newest record that does not verify raises :class:`Refused`: then every leg runs, and the
    caller says why. An older one that does not verify ends the search, and what is still
    unknown runs.
    """
    found: dict[str, Any] = {}
    pending: set[str] | None = None
    said = "no earlier record"
    for run_id in source.runs()[:limit]:
        names = [n for n in source.artifacts(run_id) if n.startswith(PREFIX)]
        if not names:
            continue
        try:
            if len(names) != 1:
                raise Refused(f"run {run_id} has {len(names)} records")
            record_digest = names[0][len(PREFIX) :]
            record = verify(source.record(run_id, names[0]), record_digest)
        except Refused:
            if pending is None:
                raise
            break
        if pending is None:
            pending = set(record["legs"])
            said = f"run {run_id}, record {record_digest}"
        for key in sorted(pending):
            entry = record["legs"].get(key)
            if not isinstance(entry, dict):
                continue
            resolved = _executed(source, key, entry, run_id, record_digest)
            if resolved is not None and not measured(resolved["entry"]):
                continue
            if resolved is not None:
                found[key] = resolved
            pending.discard(key)
        if not pending:
            break
    return found, said


def decide(
    key: str, inputs: Mapping[str, Any], earlier: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The carried entry when ``key``'s shard may reuse its earlier result, else None (run it)."""
    found = earlier.get(key)
    if not isinstance(found, Mapping) or not reusable(leg_of(key), found.get("entry"), inputs):
        return None
    return carried(found["entry"], int(found["run_id"]), str(found["digest"]))


def executed(inputs: Mapping[str, Any], result: str, ran_at: str) -> dict[str, Any]:
    if result not in (GREEN, RED):
        raise ValueError(f"not a result: {result!r}")
    return {"inputs": dict(inputs), "result": result, "ran_at": ran_at}


@dataclass(frozen=True)
class Action:
    leg: str
    action: str
    inputs: str
    red: tuple[str, ...]
    carried: bool


def settle_record(
    entries: Mapping[str, dict[str, Any]],
    open_red: Mapping[str, frozenset[str] | None],
    *,
    run_id: int,
    tree: str,
    unsettled: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], list[Action]]:
    """The run's record and what each leg does to its issue.

    ``open_red`` maps a leg to every inputs key its open issue's reds ran on (None: no open
    issue). A leg every shard of which was carried changes no issue. A flake, a green on a
    red's very inputs, is recorded red on every shard that ran, marked ``flake``.

    A leg in ``unsettled`` is one whose issue could not be read, so whether a green is a flake
    is not known: each green shard that ran is recorded red, marked ``unsettled``, which no
    later run carries, so it runs again. A red it measured stays a plain red, and opens or
    updates the issue.
    """
    legs: dict[str, dict[str, dict[str, Any]]] = {}
    for key, entry in entries.items():
        legs.setdefault(leg_of(key), {})[key] = dict(entry)
    record_legs: dict[str, Any] = {}
    actions: list[Action] = []
    for leg, shards in sorted(legs.items()):
        red = tuple(sorted(k for k, e in shards.items() if e.get("result") == RED))
        everything_carried = all(e.get("reused_from") for e in shards.values())
        inputs = {k: e.get("inputs") for k, e in sorted(shards.items())}
        key = inputs_key(inputs)
        if everything_carried:
            action = "none"
        elif leg in unsettled:
            ran_red = any(
                e.get("result") == RED and not e.get("reused_from") for e in shards.values()
            )
            action = "open" if ran_red else "none"
            for entry in shards.values():
                if not entry.get("reused_from") and entry.get("result") == GREEN:
                    entry["result"], entry["unsettled"] = RED, True
            red = tuple(sorted(shards))
        else:
            settled = settle(RED if red else GREEN, inputs, open_red.get(leg))
            action = settled.action
            if action == "flake":
                for entry in shards.values():
                    if not entry.get("reused_from"):
                        entry["result"], entry["flake"] = RED, True
        actions.append(Action(leg, action, key, red, everything_carried))
        record_legs.update(shards)
    return {"version": VERSION, "run_id": run_id, "tree": tree, "legs": record_legs}, actions


class Issues(Protocol):
    def find(self, title: str) -> tuple[int, str] | None:
        """The open issue with exactly this title: its number and all its text."""
        ...

    def create(self, title: str, body: str) -> None: ...

    def comment(self, number: int, body: str) -> None: ...

    def close(self, number: int, body: str) -> None: ...


def open_red_inputs(text: str) -> frozenset[str] | None:
    """Every inputs key the issue's recorded reds ran on, or None when it records none."""
    found = {line[len(MARKER) :].strip() for line in text.splitlines() if line.startswith(MARKER)}
    return frozenset(found) if found else None


def apply(actions: list[Action], issues: Issues, *, run_url: str, record_digest: str) -> list[str]:
    """Open, update or close each leg's issue. Returns what was done, one line a leg."""
    done = []
    for a in actions:
        title = issue_title(a.leg)
        where = f"Run: {run_url}\nRecord: `{artifact_name(record_digest)}`\n"
        if a.action == "none":
            continue
        found = issues.find(title)
        if a.action in ("open", "update"):
            body = f"Red shards: {', '.join(f'`{k}`' for k in a.red)}\n{where}{MARKER}{a.inputs}\n"
            if found is None:
                issues.create(title, f"`{a.leg}` is red in breadth.\n\n{body}")
            else:
                issues.comment(found[0], body)
        elif a.action == "flake" and found is not None:
            issues.comment(
                found[0],
                "Green on the inputs the red ran on: a flake. The record keeps the leg red, and "
                f"this stays open until a fix or a quarantine entry.\n{where}",
            )
        elif a.action == "close" and found is not None:
            issues.close(found[0], f"Green on changed inputs.\n{where}")
        done.append(f"{a.leg}: {a.action}")
    return done


# ── the command line ───────────────────────────────────────────────────────────────────────────


def _gh(*args: str) -> str:
    done = subprocess.run(
        ["gh", *args], capture_output=True, text=True, errors="replace", check=True
    )
    return done.stdout


class GhSource:
    """Earlier completed runs: on ``main``, main's own; on another ref (a release gate's tag),
    that ref's own first, then main's. A tag's earlier red is then carried, never run again on
    the same inputs; main never reads another ref's records."""

    def __init__(self, repo: str, current_run: int, ref_name: str = "main") -> None:
        self.repo, self.current_run, self.ref_name = repo, current_run, ref_name

    def runs(self) -> list[int]:
        branches = ["main"] if self.ref_name == "main" else [self.ref_name, "main"]
        found: list[int] = []
        for branch in branches:
            out = _gh(
                "api",
                f"repos/{self.repo}/actions/workflows/breadth.yml/runs"
                f"?branch={urllib.parse.quote(branch, safe='')}&status=completed&per_page=50",
                "--jq",
                ".workflow_runs[].id",
            )
            found += [int(r) for r in out.split() if int(r) != self.current_run]
        return found

    def artifacts(self, run_id: int) -> list[str]:
        out = _gh(
            "api",
            f"repos/{self.repo}/actions/runs/{run_id}/artifacts?per_page=100",
            "--jq",
            ".artifacts[] | select(.expired | not) | .name",
        )
        return out.split()

    def record(self, run_id: int, name: str) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            _gh("run", "download", str(run_id), "-R", self.repo, "--name", name, "--dir", tmp)
            return (Path(tmp) / "record.json").read_bytes()


#: The account the workflow token writes issues as. Only its issues and comments are read: on a
#: public repository anyone may comment, and a ``breadth-inputs:`` line from anyone else would
#: decide whether a green is a fix or a flake.
BOT = "github-actions[bot]"


def bot_text(issue: Mapping[str, Any], comments: list[Mapping[str, Any]]) -> str | None:
    """The issue's text written by :data:`BOT`, or None when someone else opened it."""
    if not _by_bot(issue.get("user")):
        return None
    texts = [str(issue.get("body") or "")]
    texts += [str(c.get("body") or "") for c in comments if _by_bot(c.get("user"))]
    return "\n".join(texts)


def _by_bot(user: object) -> bool:
    return isinstance(user, Mapping) and user.get("login") == BOT and user.get("type") == "Bot"


#: The one search page ``find`` reads. A full page fails closed: an issue past it is unseen.
SEARCH_PAGE = 100


class GhIssues:
    def __init__(self, repo: str) -> None:
        self.repo = repo

    def find(self, title: str) -> tuple[int, str] | None:
        """The bot's open issue titled ``title``. Raises ValueError when the search's one page
        may not hold every match: a full page, or GitHub saying its results are incomplete."""
        query = urllib.parse.quote(
            f'repo:{self.repo} is:issue is:open author:app/github-actions in:title "{title}"'
        )
        page = json.loads(_gh("api", f"search/issues?q={query}&per_page={SEARCH_PAGE}") or "{}")
        items = page.get("items") if isinstance(page, dict) else None
        if not isinstance(items, list) or page.get("incomplete_results") is not False:
            raise ValueError(f"the search for {title!r} is not one complete answer")
        if len(items) >= SEARCH_PAGE:
            raise ValueError(f"the search for {title!r} filled its page, so an issue may be unseen")
        for issue in items:
            if issue.get("title") != title or not _by_bot(issue.get("user")):
                continue
            number = int(issue["number"])
            raw = _gh(
                "api",
                "--paginate",
                "--slurp",
                f"repos/{self.repo}/issues/{number}/comments?per_page=100",
            )
            comments = [c for page in json.loads(raw or "[]") for c in page]
            text = bot_text(issue, comments)
            if text is not None:
                return number, text
        return None

    def create(self, title: str, body: str) -> None:
        _gh("issue", "create", "-R", self.repo, "--title", title, "--body", body)

    def comment(self, number: int, body: str) -> None:
        _gh("issue", "comment", str(number), "-R", self.repo, "--body", body)

    def close(self, number: int, body: str) -> None:
        _gh("issue", "close", str(number), "-R", self.repo, "--comment", body)


def _output(line: str) -> None:
    print(line)
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _summary(text: str) -> None:
    print(text)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def _load(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: str, value: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inputs", help="a shard's inputs, as its runner reports them")
    for name in ("tree", "image", "image-version", "arch", "python", "uv", "node"):
        p.add_argument(f"--{name}", required=True)
    p.add_argument("--external", action="append", default=[], metavar="NAME=VALUE")
    p.add_argument("--out", required=True)
    p = sub.add_parser("previous", help="the newest record on main, verified (freshness)")
    p.add_argument("--repo", required=True)
    p.add_argument("--run-id", type=int, required=True)
    p.add_argument("--ref", default="main", help="the ref this run is on (a tag, for a release)")
    p.add_argument("--out", required=True)
    p = sub.add_parser("decide", help="whether a shard runs, or carries its earlier result")
    p.add_argument("--key", required=True)
    p.add_argument("--inputs", required=True)
    p.add_argument("--previous", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("ran", help="the entry of a shard that ran")
    p.add_argument("--inputs", required=True)
    p.add_argument("--outcome", required=True, help="the test step's outcome")
    p.add_argument("--history", default="", help="decide's `history` output: `read`, or not")
    p.add_argument("--ref", default="main", help="the ref this run is on")
    p.add_argument("--out", required=True)
    p = sub.add_parser("night", help="the night a run belongs to, from its creation time")
    p.add_argument("--created-at", required=True, help="the run's created_at, fixed on reruns")
    p = sub.add_parser("report", help="settle the legs and write the run's record")
    p.add_argument("--repo", required=True)
    p.add_argument("--legs", required=True, help="the folder of every shard's entry")
    p.add_argument("--run-id", type=int, required=True)
    p.add_argument("--tree", required=True)
    p.add_argument("--record", required=True)
    p.add_argument("--actions", required=True)
    p = sub.add_parser("issues", help="open, update or close the leg issues")
    p.add_argument("--repo", required=True)
    p.add_argument("--actions", required=True)
    p.add_argument("--run-url", required=True)
    p.add_argument("--digest", required=True)
    args = parser.parse_args(argv)

    if args.command == "inputs":
        external = dict(item.split("=", 1) for item in args.external)
        _write(
            args.out,
            {
                "tree": args.tree,
                "image": args.image,
                "image_version": args.image_version,
                "arch": args.arch,
                "python": args.python,
                "uv": args.uv,
                "node": args.node,
                "external": external,
            },
        )
    elif args.command == "previous":
        try:
            found, said = previous(GhSource(args.repo, args.run_id, args.ref))
        except (Refused, subprocess.CalledProcessError, OSError, ValueError) as exc:
            if args.ref != "main":
                # Off main only the carry keeps a red red: no map is written, every shard's
                # decision reads none, and each green it runs is recorded `unread` (red).
                _summary(
                    f"### breadth freshness\n\nEarlier records unreadable on {args.ref}: {exc}"
                )
                raise
            found, said = {}, f"refused or unreadable, so every leg runs: {exc}"
        _write(args.out, found)
        _summary(f"### breadth freshness\n\nEarlier record: {said}. {len(found)} shard(s) known.")
    elif args.command == "decide":
        try:
            earlier = _load(args.previous)
        except (OSError, ValueError) as exc:  # freshness failed, or this shard's download did
            earlier = {}
            _summary(f"`{args.key}`: no earlier results ({exc}), so it runs.")
        else:
            if isinstance(earlier, dict):
                _output("history=read")
        entry = decide(args.key, _load(args.inputs), earlier if isinstance(earlier, dict) else {})
        if entry is None:
            _output("run=true")
        else:
            _write(args.out, entry)
            origin = entry["reused_from"]
            _summary(
                f"`{args.key}`: inputs match run {origin['run_id']}'s record "
                f"`{artifact_name(origin['digest'])}`, so its {entry['result']} is carried."
            )
            _output("run=false")
    elif args.command == "ran":
        result = GREEN if args.outcome == GREEN else RED
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = executed(_load(args.inputs), result, now)
        if result == GREEN and args.ref != "main" and args.history != "read":
            # Off main no issue holds a red's inputs, so only the carry keeps a red red. A green
            # that ran without its earlier results may be a red run again on its own inputs:
            # it is recorded red, marked `unread`, which no later run carries.
            entry["result"], entry["unread"] = RED, True
        _write(args.out, entry)
    elif args.command == "night":
        _output(f"night={night_of(args.created_at)}")
    elif args.command == "report":
        entries = {
            path.stem.replace("__", "/"): _load(str(path))
            for path in sorted(Path(args.legs).rglob("*.json"))
        }
        entries = fill_missing(entries)
        issues = GhIssues(args.repo)
        open_red: dict[str, frozenset[str] | None] = {}
        unread: set[str] = set()
        for leg in sorted({leg_of(k) for k in entries}):
            try:
                found_issue = issues.find(issue_title(leg))
            except (subprocess.CalledProcessError, OSError, ValueError, KeyError) as exc:
                # Whether a green is a flake is not known: the record keeps the leg red, marked
                # `unsettled`, so the next night runs it again rather than carrying the red.
                unread.add(leg)
                _summary(f"`{leg}`: its issue could not be read ({exc}), so it is recorded red.")
                continue
            open_red[leg] = open_red_inputs(found_issue[1]) if found_issue else None
        record, actions = settle_record(
            entries, open_red, run_id=args.run_id, tree=args.tree, unsettled=frozenset(unread)
        )
        data = canonical(record)
        Path(args.record).parent.mkdir(parents=True, exist_ok=True)
        Path(args.record).write_bytes(data)
        _write(args.actions, [a.__dict__ for a in actions])
        record_digest = digest(data)
        lines = [f"- `{a.leg}`: {a.action}{' (carried)' if a.carried else ''}" for a in actions]
        _summary(
            f"### breadth evidence\n\nRecord `{artifact_name(record_digest)}`, sha256 "
            f"`{record_digest}`. Satisfies a release on its own: "
            f"{'yes' if satisfies_release_alone(record) else 'no'}.\n\n" + "\n".join(lines)
        )
        _output(f"digest={record_digest}")
    elif args.command == "issues":
        acts = [Action(**{**a, "red": tuple(a["red"])}) for a in _load(args.actions)]
        for line in apply(
            acts, GhIssues(args.repo), run_url=args.run_url, record_digest=args.digest
        ):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
