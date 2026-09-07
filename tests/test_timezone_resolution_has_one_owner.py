"""Rail: exactly ONE module in the core resolves a wall-clock timezone (#2520).

The defect the issue filed was a wrong default. The COST it named was different: "every
trigger author now has to rediscover this pattern independently", and one first-party
surface already had. Derived by AST + interprocedural taint over `src/personalclaw/` (a
fixpoint over "which functions RETURN a tzinfo or a zone name", not a grep for one name),
the pre-fix population was **seven** hand-rolled resolvers that did not agree:

    src/personalclaw/triggers/arm.py:44                _trigger_tz()      → UTC
    src/personalclaw/schedule.py:432                   get_local_tz()     → UTC   (!)
    src/personalclaw/schedule.py:445                   _job_tz()          → UTC
    src/personalclaw/triggers/calendar.py:564          _resolve_zone()    → server-local
    src/personalclaw/knowledge/research_reports.py:396 _report_tz()       → UTC
    src/personalclaw/knowledge/report_schedules.py:76  _effective_tz()    → "" (= UTC)
    src/personalclaw/cli_setup.py:292      _detect_system_timezone()      → own /etc reader

The last two are why a name-based search is not enough: neither constructs a `ZoneInfo`, so
neither appears in `grep -rn ZoneInfo`. `_effective_tz` was found by the taint fixpoint (it
returns a zone NAME) and `_detect_system_timezone` by the `/etc/localtime` clause below.

So this rail is not "the default is right" — that is
`test_timed_trigger_timezone_default.py`. This one reds when a NEW site learns to resolve a
timezone on its own. There are exactly three ways to obtain a machine or IANA zone in this
codebase, and all three are checked:

  1. constructing a `ZoneInfo` — the only stdlib route to an IANA zone;
  2. reading `/etc/localtime` or `/etc/timezone` — the only route to the machine's zone;
  3. `time.tzname` — banned outright, everywhere, including the owner: it yields
     abbreviations (`PDT`, `CEST`) that `ZoneInfo` rejects, and reaching for it is the
     specific wrong turn the issue paid to discover.

**Vacuity floor, both directions**, because a scanner that silently matches nothing is a
green rail that checks nothing:

  * `test_the_rail_reds_on_*` plants each of the three shapes in a temp tree and asserts the
    scanner reports it. Three canaries, one per clause.
  * `test_the_scan_is_not_vacuous_*` asserts an absolute lower bound on what the scan finds,
    cross-checked against a SECOND mechanism (plain substring counting over the file bytes)
    that shares no code with the AST walk that derives the expected set.

The scanner matches the honest second implementation, not an adversarial one: a resolver that
writes `"/etc/" + "localtime"` or reaches `ZoneInfo` through `getattr` will slip past. That is
the right bar — the failure this exists to prevent is a well-meaning author solving the
problem again, not someone hiding it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: The one module allowed to resolve a zone. Everything else calls into it.
OWNER = "timezones.py"

#: Filesystem paths that name the machine's zone. Matched as EXACT string constants so a
#: docstring mentioning `/etc/localtime` (several now do, explaining this rail) is not a hit.
MACHINE_ZONE_PATHS = frozenset({"/etc/localtime", "/etc/timezone"})

#: Every module that legitimately resolves a zone — by CALLING the owner, never by
#: reimplementing it. This is the derived owner set from the docstring above; the rail asserts
#: each one still routes through `personalclaw.timezones`, so "rewired" cannot silently
#: regress to "rewritten".
DERIVED_CONSUMERS = (
    "triggers/arm.py",
    "schedule.py",
    "triggers/calendar.py",
    "knowledge/research_reports.py",
    "knowledge/report_schedules.py",
    "cli_setup.py",
)


@dataclass(frozen=True)
class Site:
    path: str  # relative to src/personalclaw
    line: int
    kind: str  # "ZoneInfo" | "machine-path" | "time.tzname"
    detail: str

    def __str__(self) -> str:  # pragma: no cover - failure output only
        return f"{self.path}:{self.line} {self.kind} ({self.detail})"


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def zone_resolution_sites(root: Path) -> list[Site]:
    """Every site under `root` that resolves a timezone from first principles.

    Takes a root so the same function can be pointed at a planted temp tree — the rail and
    its own falsification run identical code, which is the only way the canaries prove
    anything about the real scan.
    """
    sites: list[Site] = []
    for path in sorted(root.rglob("*.py")):
        rel = str(path.relative_to(root))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a parse failure is a separate problem
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _dotted(node.func).split(".")[-1] == "ZoneInfo":
                sites.append(Site(rel, node.lineno, "ZoneInfo", _dotted(node.func)))
            elif isinstance(node, ast.Attribute) and _dotted(node) == "time.tzname":
                sites.append(Site(rel, node.lineno, "time.tzname", "time.tzname"))
            elif isinstance(node, ast.Constant) and node.value in MACHINE_ZONE_PATHS:
                sites.append(Site(rel, node.lineno, "machine-path", str(node.value)))
    return sites


# ── the rail ──────────────────────────────────────────────────────────────────────────


def test_only_the_owner_module_resolves_a_timezone():
    strays = [s for s in zone_resolution_sites(SRC) if s.path != OWNER]
    assert not strays, (
        "a second timezone resolver appeared — route it through "
        "`personalclaw.timezones.resolve_zone` instead (#2520):\n  "
        + "\n  ".join(str(s) for s in strays)
    )


def test_time_tzname_is_banned_everywhere_including_the_owner():
    """The gotcha the issue paid for: `time.tzname` yields `('PST', 'PDT')`, and `ZoneInfo`
    rejects both. There is no correct use of it here, so the owner gets no exemption."""
    hits = [s for s in zone_resolution_sites(SRC) if s.kind == "time.tzname"]
    assert not hits, "time.tzname yields abbreviations ZoneInfo cannot resolve:\n  " + "\n  ".join(
        str(s) for s in hits
    )


def _imports_the_owner(path: Path) -> bool:
    """Whether `path` really IMPORTS the owner — an AST check, not a substring one.

    It was a substring check first, and a mutant that gutted
    `cli_setup._detect_system_timezone` to `return ""` survived it: the function's own
    docstring still said `timezones.machine_zone_name`, so the prose satisfied the rail while
    the delegation was gone. Prose cannot satisfy an `ast.ImportFrom`.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "personalclaw.timezones":
            return True
        if isinstance(node, ast.Import) and any(
            alias.name == "personalclaw.timezones" for alias in node.names
        ):
            return True
    return False


@pytest.mark.parametrize("module", DERIVED_CONSUMERS)
def test_every_derived_consumer_calls_the_owner(module):
    """The positive half. Passing `test_only_the_owner_module_resolves_a_timezone` by deleting
    the resolution instead of delegating it would be a regression this catches.

    Granularity is per MODULE, not per function: it asserts the module still routes through the
    owner, not that a specific line does. A module that keeps one owner import and hand-rolls a
    second resolver beside it is caught by the negative half above instead.
    """
    assert _imports_the_owner(SRC / module), (
        f"{module} is a derived timezone-resolution site and must import "
        "`personalclaw.timezones`; it has no import of the owner"
    )


# ── vacuity floor A: the scanner really reds on a planted resolver ─────────────────────


def test_the_rail_reds_on_a_planted_zoneinfo_resolver(tmp_path):
    (tmp_path / "sneaky.py").write_text(
        "from zoneinfo import ZoneInfo\n\n\ndef _my_tz(name):\n    return ZoneInfo(name)\n",
        encoding="utf-8",
    )
    found = zone_resolution_sites(tmp_path)
    assert [(s.path, s.kind) for s in found] == [("sneaky.py", "ZoneInfo")], found


def test_the_rail_reds_on_a_planted_machine_zone_reader(tmp_path):
    (tmp_path / "sneaky.py").write_text(
        "import os\n\n\ndef _my_tz():\n    return os.readlink('/etc/localtime')\n",
        encoding="utf-8",
    )
    found = zone_resolution_sites(tmp_path)
    assert [(s.path, s.kind) for s in found] == [("sneaky.py", "machine-path")], found


def test_the_rail_reds_on_a_planted_time_tzname_reader(tmp_path):
    (tmp_path / "sneaky.py").write_text(
        "import time\n\n\ndef _my_tz():\n    return time.tzname[0]\n", encoding="utf-8"
    )
    found = zone_resolution_sites(tmp_path)
    assert [(s.path, s.kind) for s in found] == [("sneaky.py", "time.tzname")], found


def test_the_rail_is_quiet_on_a_module_that_only_MENTIONS_the_paths(tmp_path):
    """A docstring explaining the rail must not trip it — several now do."""
    (tmp_path / "innocent.py").write_text(
        '"""We resolve through /etc/localtime, see personalclaw.timezones."""\n'
        "\n\ndef f():\n    return 1\n",
        encoding="utf-8",
    )
    assert zone_resolution_sites(tmp_path) == []


# ── vacuity floor B: the scan cannot be green by matching nothing ──────────────────────


def test_the_scan_is_not_vacuous_it_finds_the_owners_own_resolutions():
    """An absolute lower bound, cross-checked against a mechanism that shares no code with
    the AST walk: substring counting over the raw file bytes.

    Without this, deleting the body of `zone_resolution_sites` would make every rail above
    pass — a green suite asserting nothing about the real package.
    """
    sites = zone_resolution_sites(SRC)
    owned = [s for s in sites if s.path == OWNER and s.kind == "ZoneInfo"]
    assert len(owned) >= 3, f"the owner constructs zones; the scan found {len(owned)}: {sites}"

    machine = [s for s in sites if s.path == OWNER and s.kind == "machine-path"]
    assert len(machine) >= 2, f"the owner reads /etc/localtime AND /etc/timezone: {machine}"

    # Independent mechanism — plain text, no `ast` involved.
    raw = (SRC / OWNER).read_text(encoding="utf-8")
    assert raw.count("ZoneInfo(") >= 3
    assert raw.count('"/etc/localtime"') >= 1
    assert raw.count('"/etc/timezone"') >= 1


def test_the_scan_really_visits_the_whole_package():
    """The other way a scan goes vacuous: a wrong root, so it parses ~nothing. Counted by
    glob rather than by the parse, and floored well under the real number (1099 files at the
    time of writing) so ordinary growth or pruning does not make this a maintenance tax."""
    parsed = sum(1 for _ in SRC.rglob("*.py"))
    assert parsed >= 400, f"only {parsed} modules under {SRC} — is the root right?"
    assert (SRC / OWNER).is_file(), f"the owner module is missing from {SRC}"
