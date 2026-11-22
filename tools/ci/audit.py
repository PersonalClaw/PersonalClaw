"""``python -m tools.ci audit``: the dependency audit of the graphs core ships (#3918).

What is audited is what ships, read from core's locks and never from a fresh resolution:

- each Python graph is ``uv export --locked`` with the extras that artifact installs (the wheel,
  the gateway image, the desktop bundle; see ``PYTHON_GRAPHS``), audited by ``pip-audit`` with
  ``--no-deps --disable-pip``, so a lock pinned to an older vulnerable release is reported even
  when a fresh resolution would be clean. Each pin's environment marker is read for the platforms
  that artifact installs on (``TARGETS``), not for the runner the audit runs on, so the desktop
  bundle's macOS-only pins are audited on a Linux runner too;
- the npm graphs are the whole root ``package-lock.json`` (``npm audit --package-lock-only``):
  the web, desktop and mobile workspaces, build tools included, since they run in CI and shape
  the artifacts; and each image's own lock under ``deploy/docker/`` (today the gateway image's
  agent CLIs), audited from its folder with ``--prefix``, since the image installs it with
  ``npm ci``. See ``npm_graphs``.

The apps' and the website's locks live in their own repositories, which audit them; the
images' OS packages need a scanner of their own. Neither is read here.

The verdict:

- a tool that fails to run (a non-zero exit other than "found something", no report, or a
  report that cannot be read) fails the job, exit 2: a missing audit is never a clean one;
- a tool that could not reach its advisory service (PyPI's JSON API for pip-audit, the npm
  registry's audit endpoint for npm audit) is tried again, after 15, 45 and 90 s, at most three
  more tries in the whole run (``FETCH_WAITS``). If it still cannot reach it, the job fails,
  exit 2, under the ``infrastructure`` title (#5848), so the merge verdict reads it as the
  runner's network and not the change. That is a closed list of what each tool writes when the
  network failed (``_PIP_UNREACHED``, ``_NPM_UNREACHED``); any other failure is a plain one;
- a high or critical finding that no live waiver covers fails it, exit 1. ``pip-audit``
  reports no severity, so a Python finding is ``unrated`` and gates as if it were high;
- a waiver that is expired, more than ``MAX_DAYS`` out, missing a field, or matching no
  finding fails it too, exit 1, so the list cannot rot;
- every other finding (moderate, low) is reported in the job summary and gates nothing.

Waivers are ``[[waiver]]`` entries in ``tools/ci/audit-baseline.toml``, in the form
``tools/ci/supply-chain.toml`` uses for its exceptions: ``file``, ``scope``, ``reference``,
``owner``, ``reason`` and ``expires``. The release gate that refuses an unwaived critical is
#3911's; it reads the same verdict.
"""

from __future__ import annotations

import argparse
import datetime
import functools
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Each Python graph core ships, as the extras its artifact installs from ``uv.lock``. The
#: gateway image's are ``deploy/docker/Dockerfile.backend``'s ``uv export``; the desktop
#: bundle's are ``release.yml``'s ``uv sync`` for the bundled backend; the wheel installs none.
#: ``tests/test_ci_audit.py`` holds each to its source.
PYTHON_GRAPHS: dict[str, tuple[str, ...]] = {
    "wheel": (),
    "gateway-image": (
        "slack",
        "eval",
        "httpx",
        "anthropic",
        "openai",
        "embeddings",
        "stt",
        "tts",
    ),
    "desktop-bundle": ("anthropic", "openai", "slack"),
}

#: Where each graph's artifact installs: its platforms, as (``platform.system()``,
#: ``platform.machine()``), and the CPython releases it runs on. ``uv export`` keeps each pin's
#: environment marker, and pip-audit would read it for the Linux runner it runs on, so the audit
#: reads it for these instead (``for_targets``). The desktop bundle is each release-artifacts.yml
#: leg that builds it (the two macOS dmgs and the Linux x86-64 AppImage and .deb) and their
#: Python; the gateway image is both arches that workflow builds, on Dockerfile.backend's python
#: base; the wheel is pyproject's POSIX classifier, on both arches of each, at its two Pythons
#: (as CI's pinned releases). ``tests/rails/test_ci_audit_tree.py`` holds each to its source.
TARGETS: dict[str, tuple[tuple[tuple[str, str], ...], tuple[str, ...]]] = {
    "wheel": (
        (("Linux", "x86_64"), ("Linux", "aarch64"), ("Darwin", "arm64"), ("Darwin", "x86_64")),
        ("3.12.15", "3.13.16"),
    ),
    "gateway-image": ((("Linux", "x86_64"), ("Linux", "aarch64")), ("3.13.16",)),
    "desktop-bundle": (
        (("Darwin", "arm64"), ("Darwin", "x86_64"), ("Linux", "x86_64")),
        ("3.12.15",),
    ),
}
_SYS_PLATFORM = {"Linux": "linux", "Darwin": "darwin"}
_OS_NAME = {"Linux": "Linux", "Darwin": "macOS"}
#: The words of a marker that are not variables; a marker naming any variable a target does not
#: set (an OS build's ``platform_release`` or ``platform_version``, an ``extra``) fails the audit
#: rather than being read for the runner.
_MARKER_WORDS = frozenset({"and", "or", "not", "in"})

#: The severities a finding fails the run at, when no live waiver covers it.
GATING = frozenset({"high", "critical"})

#: A Python finding's severity: pip-audit reports none, so it gates as a high one would.
UNRATED = "unrated"

#: A waiver expires at most this many days after it is written or renewed.
MAX_DAYS = 30

FIELDS = ("file", "scope", "reference", "owner", "reason", "expires")
SCOPES = ("pip-audit", "npm-audit")

BASELINE = Path(__file__).with_name("audit-baseline.toml")
REPO = Path(__file__).resolve().parents[2]
#: Where an image keeps the npm lock it installs its tools from (tools/ci/supply_chain.py's
#: run-install rule holds that each such lock is installed by `npm ci`).
IMAGE_FOLDER = "deploy/docker"

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]
Sleep = Callable[[float], None]

#: The seconds the audit waits before each new try of a tool that could not reach its advisory
#: service, shared by the whole run: three more tries at most, about 2.5 minutes of waiting, well
#: inside the job's bound however many graphs the outage reaches.
FETCH_WAITS = (15, 45, 90)
#: The annotation title the merge verdict reads as the runner's, not the change's
#: (``tools/ci/merge_controller.py``'s ``INFRA_TITLE``).
INFRA_TITLE = "infrastructure"
#: What pip-audit writes when it could not reach PyPI: requests' and urllib3's network errors
#: (uncaught, so they end its traceback) and pip-audit's own two for a feed it cannot reach.
_PIP_UNREACHED = re.compile(
    r"requests\.exceptions\.(?:ConnectionError|ConnectTimeout|ReadTimeout|SSLError"
    r"|ChunkedEncodingError)\b"
    r"|urllib3\.exceptions\.(?:MaxRetryError|NewConnectionError|ProtocolError|ReadTimeoutError)\b"
    r"|Could not connect to PyPI's vulnerability feed|PyPI is not redirecting properly"
)
#: The error codes npm gives an audit whose registry it could not reach or that answered with a
#: server error: in its JSON report's ``error.code``, or on stderr as ``npm error code <code>``.
_NPM_UNREACHED = frozenset(
    {"EAI_AGAIN", "ECONNREFUSED", "ECONNRESET", "ENOTFOUND", "ESOCKETTIMEDOUT", "ETIMEDOUT"}
    | {"E429", "E500", "E502", "E503", "E504"}
)
_NPM_CODE = re.compile(r"npm (?:error|ERR!) code (E[A-Z0-9_]+)")


class ToolFailure(Exception):
    """An audit tool did not run to a readable report."""


class Unreachable(ToolFailure):
    """An audit tool could not reach its advisory service, on its last try."""

    def __init__(self, tool: str, target: str, detail: str) -> None:
        super().__init__(f"{tool} on {target} could not reach its advisory service: {detail}")
        self.tool, self.target = tool, target


@dataclass
class Tries:
    """The run's tries left, shared by every tool: each new try waits its turn's seconds."""

    sleep: Sleep
    waits: list[float]

    def run(
        self,
        once: Callable[[], "subprocess.CompletedProcess[str]"],
        unreached: Callable[["subprocess.CompletedProcess[str]"], bool],
    ) -> "subprocess.CompletedProcess[str]":
        """*once*'s answer, tried again while *unreached* holds and a try is left."""
        proc = once()
        while unreached(proc) and self.waits:
            self.sleep(self.waits.pop(0))
            proc = once()
        return proc


def _pip_unreached(proc: "subprocess.CompletedProcess[str]") -> bool:
    return bool(_PIP_UNREACHED.search(f"{proc.stderr or ''}\n{proc.stdout or ''}"))


def _npm_unreached(proc: "subprocess.CompletedProcess[str]") -> bool:
    if proc.returncode == 0:
        return False
    try:
        doc = json.loads(proc.stdout or "")
    except ValueError:
        doc = None
    error = doc.get("error") if isinstance(doc, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code in _NPM_UNREACHED or any(
        c in _NPM_UNREACHED for c in _NPM_CODE.findall(proc.stderr or "")
    )


@dataclass(frozen=True)
class Finding:
    """One advisory against one package of one graph."""

    file: str
    graph: str
    package: str
    version: str
    advisory: str
    severity: str

    @property
    def scope(self) -> str:
        return "pip-audit" if self.file == "uv.lock" else "npm-audit"

    @property
    def reference(self) -> str:
        return f"{self.package} {self.advisory}"

    @property
    def gates(self) -> bool:
        return self.severity in GATING or self.severity == UNRATED


@dataclass(frozen=True)
class Verdict:
    findings: tuple[Finding, ...]
    gating: tuple[Finding, ...]
    waived: tuple[Finding, ...]
    problems: tuple[str, ...]

    @property
    def failed(self) -> bool:
        return bool(self.gating or self.problems)


# ── reading the tools' reports ────────────────────────────────────────────────────────────────


def _json(text: str, what: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as err:
        raise ToolFailure(f"{what}: the report is not JSON ({err})") from None


def pip_findings(graph: str, text: str) -> tuple[list[Finding], list[str]]:
    """The findings in a ``pip-audit -f json`` report, and the packages it could not audit."""
    doc = _json(text, f"pip-audit on {graph}")
    deps = doc.get("dependencies") if isinstance(doc, dict) else None
    if not isinstance(deps, list) or not deps:
        raise ToolFailure(f"pip-audit on {graph}: the report lists no dependencies")
    findings: list[Finding] = []
    skipped: list[str] = []
    for dep in deps:
        if not isinstance(dep, dict) or not isinstance(dep.get("name"), str):
            raise ToolFailure(f"pip-audit on {graph}: a dependency entry has no name")
        if "skip_reason" in dep:
            skipped.append(f"{graph}: {dep['skip_reason']}")
            continue
        for vuln in dep.get("vulns") or []:
            aliases = [a for a in vuln.get("aliases") or [] if str(a).startswith("GHSA-")]
            advisory = str(aliases[0] if aliases else vuln.get("id", "?"))
            findings.append(
                Finding(
                    "uv.lock", graph, dep["name"], str(dep.get("version", "?")), advisory, UNRATED
                )
            )
    return findings, skipped


def npm_findings(text: str, graph: str = "npm", lock: str = "package-lock.json") -> list[Finding]:
    """The advisories in an ``npm audit --json`` report of *lock*, once each.

    npm lists a package that only depends on a vulnerable one as vulnerable too; only the
    entries ``via`` names as an advisory are findings, so each advisory is counted once."""
    doc = _json(text, f"npm audit of {lock}")
    if not isinstance(doc, dict) or "error" in doc:
        detail = doc.get("error") if isinstance(doc, dict) else doc
        raise ToolFailure(f"npm audit of {lock}: it reported an error, not an audit ({detail})")
    vulns, meta = doc.get("vulnerabilities"), doc.get("metadata")
    if not isinstance(vulns, dict) or not isinstance(meta, dict) or "vulnerabilities" not in meta:
        raise ToolFailure(f"npm audit of {lock}: the report has no vulnerabilities or metadata")
    found: dict[tuple[str, str], Finding] = {}
    for name, vuln in vulns.items():
        for via in vuln.get("via") or [] if isinstance(vuln, dict) else []:
            if not isinstance(via, dict):
                continue
            url = str(via.get("url", ""))
            advisory = url.rsplit("/", 1)[-1] if url else str(via.get("source", "?"))
            package = str(via.get("name", name))
            found[(package, advisory)] = Finding(
                lock,
                graph,
                package,
                str(via.get("range", "?")),
                advisory,
                str(via.get("severity", UNRATED)),
            )
    return sorted(found.values(), key=lambda f: (f.package, f.advisory))


# ── the waivers ───────────────────────────────────────────────────────────────────────────────


def load_waivers(path: Path) -> list[dict[str, Any]]:
    entries = tomllib.loads(path.read_text(encoding="utf-8")).get("waiver", [])
    if not isinstance(entries, list):
        raise ToolFailure(f"{path.name}: `waiver` is not a list of tables")
    return entries


def judge(
    findings: Sequence[Finding], waivers: Sequence[Mapping[str, Any]], today: datetime.date
) -> Verdict:
    problems: list[str] = []
    waived: set[tuple[str, str, str]] = set()
    for i, entry in enumerate(waivers, 1):
        where = f"waiver {i} ({entry.get('reference', '?')})"
        missing = [
            f
            for f in FIELDS
            if f not in entry
            or (f != "expires" and not (isinstance(entry[f], str) and entry[f].strip()))
        ]
        if missing:
            problems.append(f"{where}: missing {', '.join(missing)}")
            continue
        expires = entry["expires"]
        if type(expires) is not datetime.date:
            problems.append(f"{where}: expires must be a date, such as {today.isoformat()}")
            continue
        if entry["scope"] not in SCOPES:
            problems.append(f"{where}: scope must be one of {', '.join(SCOPES)}")
            continue
        if expires < today:
            problems.append(f"{where}: expired on {expires.isoformat()}: fix it, or renew it")
            continue
        if (expires - today).days > MAX_DAYS:
            problems.append(
                f"{where}: expires {expires.isoformat()}, more than {MAX_DAYS} days out"
            )
            continue
        key = (entry["scope"], entry["file"], entry["reference"])
        if not any((f.scope, f.file, f.reference) == key for f in findings):
            problems.append(f"{where}: matches no finding, so it is stale: delete it")
            continue
        waived.add(key)
    covered = [f for f in findings if (f.scope, f.file, f.reference) in waived]
    gating = [f for f in findings if f.gates and f not in covered]
    return Verdict(tuple(findings), tuple(gating), tuple(covered), tuple(problems))


# ── running the tools ─────────────────────────────────────────────────────────────────────────


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), capture_output=True, text=True, check=False)


def _tail(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stderr or proc.stdout or "").strip()[-600:]


def export_argv(extras: Sequence[str], out: Path) -> list[str]:
    """The locked export of one graph: the lock's exact pins, never a fresh resolution."""
    argv = ["uv", "export", "--locked", "--no-emit-project", "--format", "requirements-txt"]
    for extra in extras:
        argv += ["--extra", extra]
    return [*argv, "--output-file", str(out)]


def environments(graph: str) -> list[dict[str, str]]:
    """The marker environment of each target *graph*'s artifact installs on (PEP 508's variables,
    all but an OS build's ``platform_release`` and ``platform_version``)."""
    platforms, pythons = TARGETS[graph]
    return [
        {
            "os_name": "posix",
            "sys_platform": _SYS_PLATFORM[system],
            "platform_system": system,
            "platform_machine": machine,
            "platform_python_implementation": "CPython",
            "implementation_name": "cpython",
            "implementation_version": python,
            "python_full_version": python,
            "python_version": python.rpartition(".")[0],
        }
        for system, machine in platforms
        for python in pythons
    ]


def for_targets(graph: str, export: str) -> list[str]:
    """The pins of *graph*'s ``uv export`` that install on any of its targets, each without its
    marker, so pip-audit audits them wherever it runs, and none of the rest. They come as one
    requirements file's text or more, as no file names a package twice: a lock can pin one package
    at two versions, one for each platform (torch's CPU build on Linux)."""
    # pip-audit's own dependency: the audit runs in pip-audit's venv, and every other command of
    # `python -m tools.ci`, which imports this module, on a bare python3.
    from packaging.markers import InvalidMarker, Marker

    envs = environments(graph)
    entries: list[list[str]] = []
    for line in export.splitlines():
        if line[:1].isspace() and entries:
            entries[-1].append(line)  # a pin's hashes and its `# via` lines
        elif line.strip() and not line.lstrip().startswith("#"):
            entries.append([line])
    files: list[dict[str, str]] = []
    for head, *rest in entries:
        continued = head.rstrip().endswith("\\")
        pin, _, text = head.rstrip().removesuffix("\\").partition(";")
        pin = pin.strip()
        name = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", pin)
        if not name:
            raise ToolFailure(f"uv export for {graph}: a line names no package: {head!r}")
        if text.strip():
            try:
                marker = Marker(text.strip())
            except InvalidMarker as err:
                raise ToolFailure(f"uv export for {graph}: {pin}'s marker: {err}") from None
            unquoted = re.sub(r"'[^']*'|\"[^\"]*\"", "", str(marker))
            unknown = set(re.findall(r"[A-Za-z_.]+", unquoted)) - _MARKER_WORDS - set(envs[0])
            if unknown:
                raise ToolFailure(
                    f"uv export for {graph}: {pin}'s marker reads {', '.join(sorted(unknown))},"
                    f" which the audit does not know for {graph}'s platforms: {marker}"
                )
            if not any(marker.evaluate(env) for env in envs):
                continue
        key = re.sub(r"[-_.]+", "-", name.group(0)).lower()
        entry = "\n".join([f"{pin} \\" if continued else pin, *rest])
        free = next((f for f in files if key not in f), None)
        if free is None:
            free = {}
            files.append(free)
        free[key] = entry
    return ["\n".join(f.values()) + "\n" for f in files]


def platforms(graph: str) -> str:
    """Where *graph* was read for, as the summary says it."""
    places, pythons = TARGETS[graph]
    where = ", ".join(f"{_OS_NAME[system]} {machine}" for system, machine in places)
    return f"{where} (Python {', '.join(pythons)})"


def audit_python(
    pip_audit_python: str, work: Path, run: Runner, tries: Tries | None = None
) -> tuple[list[Finding], list[str]]:
    tries = tries or Tries(time.sleep, list(FETCH_WAITS))
    findings: list[Finding] = []
    skipped: list[str] = []
    for graph, extras in PYTHON_GRAPHS.items():
        export = work / f"{graph}.export.txt"
        proc = run(export_argv(extras, export))
        if proc.returncode != 0:
            raise ToolFailure(f"uv export for {graph} failed: {_tail(proc)}")
        texts = for_targets(graph, export.read_text(encoding="utf-8"))
        for n, text in enumerate(texts, 1):
            part = graph if n == 1 else f"{graph}-{n}"
            req, report = work / f"{part}.txt", work / f"{part}.json"
            req.write_text(text, encoding="utf-8")
            got, skips = _pip_audit(pip_audit_python, graph, req, report, run, tries)
            findings += got
            skipped += skips
    return findings, skipped


def _pip_audit(
    pip_audit_python: str, graph: str, req: Path, report: Path, run: Runner, tries: Tries
) -> tuple[list[Finding], list[str]]:
    argv = [pip_audit_python, "-m", "pip_audit", "-r", str(req), "--no-deps", "--disable-pip"]
    argv += ["-f", "json", "-o", str(report)]

    def once() -> subprocess.CompletedProcess[str]:
        report.unlink(missing_ok=True)
        return run(argv)

    # 1 is "found something" only when the report was written; anything else is a failure.
    def failed(proc: subprocess.CompletedProcess[str]) -> bool:
        return proc.returncode not in (0, 1) or not report.is_file()

    proc = tries.run(once, lambda p: failed(p) and _pip_unreached(p))
    if failed(proc) and _pip_unreached(proc):
        raise Unreachable("pip-audit", graph, _tail(proc))
    if failed(proc):
        raise ToolFailure(f"pip-audit on {graph} wrote no report: {_tail(proc)}")
    return pip_findings(graph, report.read_text(encoding="utf-8"))


def npm_graphs(root: Path) -> list[tuple[str, str]]:
    """Each npm graph core ships, as (graph, lock), repository-relative: the root lock, and the
    lock of each folder under deploy/docker/ that holds an npm manifest or lock. A folder with
    a manifest and no lock is audited too, so the audit fails on it rather than skipping it."""
    folders = {
        path.parent.relative_to(root).as_posix()
        for name in ("package.json", "package-lock.json")
        for path in (root / IMAGE_FOLDER).glob(f"**/{name}")
        if "node_modules" not in path.parts
    }
    return [("npm", "package-lock.json")] + [(f, f"{f}/package-lock.json") for f in sorted(folders)]


def audit_npm(run: Runner, root: Path, tries: Tries | None = None) -> list[Finding]:
    tries = tries or Tries(time.sleep, list(FETCH_WAITS))
    findings: list[Finding] = []
    for graph, lock in npm_graphs(root):
        folder = lock.rpartition("/")[0]
        argv = ["npm", "audit", "--package-lock-only", "--json"]
        argv = [*argv, "--prefix", folder] if folder else argv
        proc = tries.run(functools.partial(run, argv), _npm_unreached)
        if _npm_unreached(proc):
            raise Unreachable("npm audit", lock, _tail(proc))
        if proc.returncode not in (0, 1):
            raise ToolFailure(f"npm audit of {lock} exited {proc.returncode}: {_tail(proc)}")
        findings += npm_findings(proc.stdout, graph, lock)
    return findings


# ── the report ────────────────────────────────────────────────────────────────────────────────


def summary(verdict: Verdict, skipped: Sequence[str]) -> str:
    lines = ["### Dependency audit", ""]
    lines.append(
        f"{len(verdict.findings)} finding(s): {len(verdict.gating)} that fail the run, "
        f"{len(verdict.waived)} under a live waiver."
    )
    lines += ["", "Each Python graph's markers were read for where its artifact installs:"]
    lines += [f"- {graph}: {platforms(graph)}" for graph in PYTHON_GRAPHS]
    if verdict.findings:
        lines += [
            "",
            "| Graph | Package | Version | Advisory | Severity | Status |",
            "|---|---|---|---|---|---|",
        ]
        for f in verdict.findings:
            status = (
                "**fails the run**"
                if f in verdict.gating
                else "waived" if f in verdict.waived else "reported"
            )
            cells = (f.graph, f.package, f.version, f.advisory, f.severity, status)
            lines.append("| " + " | ".join(cells) + " |")
    if verdict.problems:
        lines += ["", "**Waivers that fail the run:**", *(f"- {p}" for p in verdict.problems)]
    if skipped:
        lines += ["", "Not audited (not on PyPI):", *(f"- {s}" for s in skipped)]
    return "\n".join(lines) + "\n"


def _publish(text: str) -> None:
    print(text, end="")
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)


def main(
    argv: Sequence[str],
    run: Runner = _run,
    today: datetime.date | None = None,
    root: Path = REPO,
    sleep: Sleep = time.sleep,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.ci audit")
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run", help="audit every graph core ships and judge the findings")
    go.add_argument("--pip-audit", required=True, help="the Python pip-audit is installed in")
    go.add_argument("--work", type=Path, required=True, help="a folder for the exports")
    go.add_argument("--baseline", type=Path, default=BASELINE)
    args = parser.parse_args(list(argv))
    day = today or datetime.datetime.now(datetime.UTC).date()
    tries = Tries(sleep, list(FETCH_WAITS))
    try:
        args.work.mkdir(parents=True, exist_ok=True)
        waivers = load_waivers(args.baseline)
        python, skipped = audit_python(args.pip_audit, args.work, run, tries)
        verdict = judge([*python, *audit_npm(run, root, tries)], waivers, day)
    except Unreachable as err:
        # Still red, never green: an audit that could not reach its advisories did not run.
        where = f"{err.tool} on {err.target}"
        print(
            f"::error title={INFRA_TITLE}::the dependency audit could not reach its advisory"
            f" service ({where})"
        )
        _publish(
            "### Dependency audit\n\n**The audit could not reach its advisory service**, after"
            f" {len(FETCH_WAITS) - len(tries.waits)} more tries in this run: {err}\n"
        )
        return 2
    except (ToolFailure, OSError, tomllib.TOMLDecodeError) as err:
        print(f"::error title=the dependency audit did not run::{err}")
        _publish(f"### Dependency audit\n\n**The audit did not run:** {err}\n")
        return 2
    _publish(summary(verdict, skipped))
    for f in verdict.gating:
        print(f"::error title=unwaived {f.severity} finding::{f.graph}: {f.reference}")
    for p in verdict.problems:
        print(f"::error title=waiver::{p}")
    return 1 if verdict.failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
