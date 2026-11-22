"""``python -m tools.ci audit``: the dependency audit of the graphs core ships (#3918).

The command is driven end to end through ``audit.main`` with a fake runner standing in for
``uv export``, ``pip-audit`` and ``npm audit``, so each test pins the wiring, not a helper.
"""

from __future__ import annotations

import datetime
import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import ci_audit_shared
import pytest

from tools.ci import audit

_no_summary = ci_audit_shared._no_summary

TODAY = datetime.date(2026, 10, 18)

CLEAN_PIP = {"dependencies": [{"name": "idna", "version": "3.7", "vulns": []}], "fixes": []}
VULN_PIP = {
    "dependencies": [
        {
            "name": "idna",
            "version": "3.6",
            "vulns": [
                {
                    "id": "PYSEC-2024-60",
                    "fix_versions": ["3.7"],
                    "aliases": ["CVE-2024-3651", "GHSA-jjg7-2v4v-x38h"],
                }
            ],
        }
    ],
    "fixes": [],
}
CLEAN_NPM = {
    "vulnerabilities": {},
    "metadata": {"vulnerabilities": {"moderate": 0, "high": 0, "critical": 0, "total": 0}},
}
#: pip-audit's stderr when PyPI did not answer, as the audit job recorded it on 2026-11-18.
PYPI_TIMEOUT = (
    "Traceback (most recent call last):\n"
    '  File "requests/adapters.py", line 713, in send\n'
    "requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='pypi.org', port=443): Read timed"
    " out. (read timeout=15)\n"
)
#: npm audit's report when the registry's audit endpoint did not answer.
NPM_TIMEOUT = {
    "error": {
        "code": "ETIMEDOUT",
        "summary": "request to https://registry.npmjs.org/-/npm/v1/security/advisories/bulk failed",
    }
}


def _npm(*advisories: tuple[str, str, str]) -> dict:
    """An ``npm audit --json`` report: each advisory as (package, GHSA id, severity), plus one
    package that only depends on the first, the way npm lists it."""
    vulns: dict = {}
    for package, ghsa, severity in advisories:
        vulns[package] = {
            "severity": severity,
            "via": [
                {
                    "source": 1,
                    "name": package,
                    "url": f"https://github.com/advisories/{ghsa}",
                    "severity": severity,
                    "range": "<=1.0.0",
                }
            ],
        }
    if advisories:
        vulns["a-dependent"] = {"severity": advisories[0][2], "via": [advisories[0][0]]}
    return {"vulnerabilities": vulns, "metadata": {"vulnerabilities": {"total": len(vulns)}}}


class FakeTools:
    """``uv export``, ``pip-audit`` and ``npm audit`` as the audit calls them."""

    def __init__(
        self,
        *,
        pip: dict | str | Callable[[str], dict] | None = None,
        npm: dict | str | None = None,
        image_npm: dict | str | None = None,
        pip_exit: int | None = None,
        npm_exit: int | None = None,
        image_npm_exit: int | None = None,
        export_exit: int = 0,
        pip_down: int = 0,
        npm_down: int = 0,
        pip_down_with: str = PYPI_TIMEOUT,
        export: str = "idna==3.6\n",
    ) -> None:
        self.pip = CLEAN_PIP if pip is None else pip
        self.npm = CLEAN_NPM if npm is None else npm
        self.image_npm = CLEAN_NPM if image_npm is None else image_npm
        self.pip_exit, self.npm_exit, self.export_exit = pip_exit, npm_exit, export_exit
        self.image_npm_exit = image_npm_exit
        # How many of the next pip-audit and npm audit runs cannot reach their service.
        self.pip_down, self.npm_down, self.pip_down_with = pip_down, npm_down, pip_down_with
        self.export = export
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = list(argv)
        self.calls.append(argv)
        if argv[:2] == ["uv", "export"]:
            out = Path(argv[argv.index("--output-file") + 1])
            if self.export_exit == 0:
                out.write_text(self.export, encoding="utf-8")
            return subprocess.CompletedProcess(argv, self.export_exit, "", "lock is stale")
        if "pip_audit" in argv and self.pip_down:
            self.pip_down -= 1
            return subprocess.CompletedProcess(argv, 1, "", self.pip_down_with)
        if argv[:2] == ["npm", "audit"] and self.npm_down:
            self.npm_down -= 1
            return subprocess.CompletedProcess(argv, 1, json.dumps(NPM_TIMEOUT), "")
        if "pip_audit" in argv:
            report = Path(argv[argv.index("-o") + 1])
            pip = self.pip
            if callable(pip):
                pip = pip(Path(argv[argv.index("-r") + 1]).read_text(encoding="utf-8"))
            if pip is not False:
                text = pip if isinstance(pip, str) else json.dumps(pip)
                report.write_text(text, encoding="utf-8")
            vulnerable = isinstance(pip, dict) and any(d.get("vulns") for d in pip["dependencies"])
            code = self.pip_exit if self.pip_exit is not None else int(vulnerable)
            return subprocess.CompletedProcess(argv, code, "", "pip-audit crashed")
        if argv[:2] == ["npm", "audit"]:
            # The root lock is audited where the audit runs, an image's lock under --prefix.
            report, exit_code = (
                (self.image_npm, self.image_npm_exit)
                if "--prefix" in argv
                else (self.npm, self.npm_exit)
            )
            text = report if isinstance(report, str) else json.dumps(report)
            found = isinstance(report, dict) and bool(report.get("vulnerabilities"))
            code = exit_code if exit_code is not None else int(found)
            return subprocess.CompletedProcess(argv, code, text, "")
        raise AssertionError(f"the audit ran {argv}")


def _waiver(
    reference: str, /, *, scope: str = "npm-audit", days: int = 10, file: str = "", **drop: bool
) -> str:
    fields = {
        "file": file or ("uv.lock" if scope == "pip-audit" else "package-lock.json"),
        "scope": scope,
        "reference": reference,
        "owner": "maintainers",
        "reason": "A recorded reason.",
    }
    lines = ["[[waiver]]"]
    lines += [f'{k} = "{v}"' for k, v in fields.items() if not drop.get(k)]
    if not drop.get("expires"):
        lines.append(f"expires = {(TODAY + datetime.timedelta(days=days)).isoformat()}")
    return "\n".join(lines) + "\n"


def _never_waits(seconds: float) -> None:
    raise AssertionError(f"the audit waited {seconds} s to try a tool again")


def _run(tmp_path: Path, tools: FakeTools, baseline: str = "", monkeypatch=None, sleep=None):
    """The audit's run; by default it must never wait to try a tool again."""
    path = tmp_path / "audit-baseline.toml"
    path.write_text(baseline, encoding="utf-8")
    argv = ["run", "--pip-audit", "py", "--work", str(tmp_path / "work"), "--baseline", str(path)]
    return audit.main(
        argv, run=tools, today=TODAY, root=tmp_path / "tree", sleep=sleep or _never_waits
    )


#: An image's own npm lock, in the planted tree the audit reads: what that image installs.
_IMAGE = "deploy/docker/planted"


def _image_lock(tmp_path: Path, *, lock: bool = True) -> None:
    folder = tmp_path / "tree" / _IMAGE
    folder.mkdir(parents=True)
    (folder / "package.json").write_text('{"dependencies": {"left-pad": "1.3.0"}}\n')
    if lock:
        (folder / "package-lock.json").write_text('{"lockfileVersion": 3, "packages": {}}\n')


# ── what is audited ───────────────────────────────────────────────────────────────────────────


def test_every_shipped_python_graph_is_exported_from_the_lock_and_audited_without_resolving(
    tmp_path: Path,
) -> None:
    tools = FakeTools()
    assert _run(tmp_path, tools) == 0
    exports = [c for c in tools.calls if c[:2] == ["uv", "export"]]
    assert len(exports) == len(audit.PYTHON_GRAPHS)
    for call, (graph, extras) in zip(exports, audit.PYTHON_GRAPHS.items(), strict=True):
        assert "--locked" in call, call
        assert [call[i + 1] for i, w in enumerate(call) if w == "--extra"] == list(extras), graph
    audits = [c for c in tools.calls if "pip_audit" in c]
    assert len(audits) == len(audit.PYTHON_GRAPHS)
    assert all("--no-deps" in c and "--disable-pip" in c for c in audits), audits
    assert ["npm", "audit", "--package-lock-only", "--json"] in tools.calls


def test_each_image_lock_is_audited_as_a_graph_of_its_own(tmp_path: Path, _no_summary) -> None:
    """An image's npm lock under deploy/docker/ ships in that image, so it is audited too, from
    its own folder, and its finding names that lock, not the root one."""
    _image_lock(tmp_path)
    tools = FakeTools(image_npm=_npm(("left-pad", "GHSA-aaaa-bbbb-cccc", "high")))
    assert _run(tmp_path, tools) == 1
    assert ["npm", "audit", "--package-lock-only", "--json"] in tools.calls
    assert ["npm", "audit", "--package-lock-only", "--json", "--prefix", _IMAGE] in tools.calls
    text = _no_summary.read_text(encoding="utf-8")
    assert f"| {_IMAGE} | left-pad |" in text and "**fails the run**" in text


def test_a_waiver_covers_the_image_lock_it_names_and_no_other(tmp_path: Path) -> None:
    _image_lock(tmp_path)
    high = FakeTools(image_npm=_npm(("left-pad", "GHSA-aaaa-bbbb-cccc", "high")))
    ref = "left-pad GHSA-aaaa-bbbb-cccc"
    assert _run(tmp_path, high, _waiver(ref, file=f"{_IMAGE}/package-lock.json")) == 0
    assert _run(tmp_path, high, _waiver(ref)) == 1, "a root-lock waiver is not the image's"


@pytest.mark.parametrize(
    "tools",
    [
        pytest.param(
            FakeTools(image_npm={"error": {"code": "ENOLOCK"}}, image_npm_exit=1), id="errors"
        ),
        pytest.param(FakeTools(image_npm="", image_npm_exit=1), id="prints nothing"),
        pytest.param(FakeTools(image_npm_exit=127), id="does not run"),
    ],
)
def test_an_image_lock_the_audit_cannot_read_fails_the_job(
    tmp_path: Path, tools: FakeTools, _no_summary
) -> None:
    _image_lock(tmp_path)
    assert _run(tmp_path, tools) == 2
    assert f"{_IMAGE}/package-lock.json" in _no_summary.read_text(encoding="utf-8")


def test_an_image_manifest_with_no_lock_is_audited_so_the_audit_fails(tmp_path: Path) -> None:
    """A manifest with no lock beside it is not skipped: npm cannot audit it, which fails."""
    _image_lock(tmp_path, lock=False)
    tools = FakeTools(image_npm={"error": {"code": "ENOLOCK"}}, image_npm_exit=1)
    assert _run(tmp_path, tools) == 2
    assert ["npm", "audit", "--package-lock-only", "--json", "--prefix", _IMAGE] in tools.calls


def test_a_lock_pinned_to_a_vulnerable_release_is_reported_and_fails(
    tmp_path: Path, _no_summary
) -> None:
    """The pins come from the lock, so an older vulnerable pin is found even when a fresh
    resolution would pick a fixed release. pip-audit reports no severity, so it gates."""
    assert _run(tmp_path, FakeTools(pip=VULN_PIP)) == 1
    text = _no_summary.read_text(encoding="utf-8")
    assert "GHSA-jjg7-2v4v-x38h" in text and "**fails the run**" in text


#: A locked export as uv writes it: a pin for every platform, one for macOS only, one for Linux on
#: x86-64 only, and one for Windows, which nothing core ships installs on.
_PLATFORM_EXPORT = (
    "# This file was autogenerated by uv via the following command:\n"
    "#    uv export --locked --no-emit-project --format requirements-txt\n"
    "idna==3.6 \\\n    --hash=sha256:aaaa\n    # via personalclaw\n"
    "pyobjc-core==10.0 ; sys_platform == 'darwin' \\\n    --hash=sha256:bbbb\n"
    "pysqlite3-binary==0.5.4 ; platform_machine == 'x86_64' and sys_platform == 'linux' \\\n"
    "    --hash=sha256:cccc\n"
    "pywin32==312 ; sys_platform == 'win32' \\\n    --hash=sha256:dddd\n"
)


def _report_of(requirements: str) -> dict:
    """pip-audit's report of a requirements file in which pyobjc-core 10.0 has an advisory. It
    is handed no marker: it would read one for the runner it runs on."""
    deps = []
    for line in requirements.splitlines():
        if line and not line[0].isspace():
            assert ";" not in line, f"pip-audit was handed a marker to read for its runner: {line}"
            name, _, version = line.split()[0].partition("==")
            vulns = [{"id": "PYSEC-0-1", "aliases": ["GHSA-plnt-plnt-plnt"]}]
            vulns = vulns if name == "pyobjc-core" else []
            deps.append({"name": name, "version": version, "vulns": vulns})
    return {"dependencies": deps, "fixes": []}


def _audited(tmp_path: Path, tools: FakeTools) -> dict[str, str]:
    """Each requirements file pip-audit was handed, by name, as it read it."""
    return {
        Path(c[c.index("-r") + 1]).stem: Path(c[c.index("-r") + 1]).read_text(encoding="utf-8")
        for c in tools.calls
        if "pip_audit" in c
    }


def test_a_pin_is_audited_for_the_platforms_its_artifact_installs_on(
    tmp_path: Path, capsys, _no_summary
) -> None:
    """pip-audit reads a marker for the runner it runs on, a Linux one, so the desktop bundle's
    macOS-only pins went unaudited. Each marker is read for the artifact's own platforms instead,
    so the planted macOS pin's advisory fails the bundle wherever the audit runs."""
    tools = FakeTools(export=_PLATFORM_EXPORT, pip=_report_of)
    assert _run(tmp_path, tools) == 1
    audited = _audited(tmp_path, tools)
    assert set(audited) == set(audit.PYTHON_GRAPHS)
    named = {
        graph: {line.split()[0] for line in text.splitlines() if line[:1].isalpha()}
        for graph, text in audited.items()
    }
    every = {"idna==3.6", "pyobjc-core==10.0", "pysqlite3-binary==0.5.4"}
    assert named == {
        "wheel": every,
        "gateway-image": {"idna==3.6", "pysqlite3-binary==0.5.4"},
        "desktop-bundle": every,
    }
    # A kept pin keeps its hashes and drops only its marker.
    assert "pyobjc-core==10.0 \\\n    --hash=sha256:bbbb" in audited["desktop-bundle"]
    errors = capsys.readouterr().out
    assert "desktop-bundle: pyobjc-core GHSA-plnt-plnt-plnt" in errors
    assert "gateway-image: pyobjc-core" not in errors
    summary = _no_summary.read_text(encoding="utf-8")
    assert "- desktop-bundle: macOS arm64, macOS x86_64, Linux x86_64 (Python 3.12.15)" in summary
    assert "- gateway-image: Linux x86_64, Linux aarch64 (Python 3.13.16)" in summary


def test_a_package_pinned_at_two_versions_by_platform_is_audited_at_each(tmp_path: Path) -> None:
    """A lock can pin one package twice, one version for each platform. No requirements file
    names a package twice, so a graph that installs both is audited in two files."""
    export = (
        "torch==2.13.0 ; sys_platform != 'linux' \\\n    --hash=sha256:aaaa\n"
        "torch==2.13.0+cpu ; sys_platform == 'linux' \\\n    --hash=sha256:bbbb\n"
        "idna==3.6\n"
    )
    tools = FakeTools(export=export, pip=_report_of)
    assert _run(tmp_path, tools) == 0
    pins = {
        name: sorted(line.split()[0] for line in text.splitlines() if line[:1].isalpha())
        for name, text in _audited(tmp_path, tools).items()
    }
    assert pins == {
        "wheel": ["idna==3.6", "torch==2.13.0"],
        "wheel-2": ["torch==2.13.0+cpu"],
        "gateway-image": ["idna==3.6", "torch==2.13.0+cpu"],
        "desktop-bundle": ["idna==3.6", "torch==2.13.0"],
        "desktop-bundle-2": ["torch==2.13.0+cpu"],
    }


@pytest.mark.parametrize(
    "marker",
    ["platform_release >= '23'", "extra == 'dev'", "platform_version == 'x'"],
    ids=["an OS release", "an extra", "an OS build"],
)
def test_a_marker_the_audit_cannot_read_for_a_platform_fails_the_job(
    tmp_path: Path, marker: str, _no_summary
) -> None:
    """A marker on what the audit does not know of a platform would be read for the runner, so
    it fails the job rather than auditing a guess."""
    tools = FakeTools(export=f"idna==3.6\nfoo==1.0 ; {marker}\n", pip=_report_of)
    assert _run(tmp_path, tools) == 2
    summary = _no_summary.read_text(encoding="utf-8")
    assert "The audit did not run" in summary and "foo==1.0's marker reads" in summary
    assert not any("pip_audit" in c for c in tools.calls)


# ── what fails the run ────────────────────────────────────────────────────────────────────────


def test_a_new_high_finding_fails_and_a_moderate_is_reported(tmp_path: Path, _no_summary) -> None:
    assert _run(tmp_path, FakeTools(npm=_npm(("left-pad", "GHSA-aaaa-bbbb-cccc", "moderate")))) == 0
    assert "GHSA-aaaa-bbbb-cccc" in _no_summary.read_text(encoding="utf-8")
    assert _run(tmp_path, FakeTools(npm=_npm(("left-pad", "GHSA-aaaa-bbbb-cccc", "high")))) == 1


def test_a_critical_finding_fails(tmp_path: Path) -> None:
    assert _run(tmp_path, FakeTools(npm=_npm(("left-pad", "GHSA-aaaa-bbbb-cccc", "critical")))) == 1


def test_a_dependent_npm_lists_is_not_counted_again(tmp_path: Path, _no_summary) -> None:
    _run(tmp_path, FakeTools(npm=_npm(("left-pad", "GHSA-aaaa-bbbb-cccc", "moderate"))))
    assert "a-dependent" not in _no_summary.read_text(encoding="utf-8")


def test_a_live_waiver_lets_a_high_finding_pass_until_it_expires(tmp_path: Path) -> None:
    high = FakeTools(npm=_npm(("left-pad", "GHSA-aaaa-bbbb-cccc", "high")))
    ref = "left-pad GHSA-aaaa-bbbb-cccc"
    assert _run(tmp_path, high, _waiver(ref, days=10)) == 0
    assert _run(tmp_path, high, _waiver(ref, days=0)) == 0, "the expiry day itself is live"
    assert _run(tmp_path, high, _waiver(ref, days=-1)) == 1, "an expired waiver is no waiver"


def test_a_waiver_on_a_python_finding_names_pip_audit(tmp_path: Path) -> None:
    ref = "idna GHSA-jjg7-2v4v-x38h"
    assert _run(tmp_path, FakeTools(pip=VULN_PIP), _waiver(ref, scope="pip-audit")) == 0


@pytest.mark.parametrize("field", audit.FIELDS)
def test_a_waiver_missing_a_field_is_refused(tmp_path: Path, field: str) -> None:
    high = FakeTools(npm=_npm(("left-pad", "GHSA-aaaa-bbbb-cccc", "high")))
    assert _run(tmp_path, high, _waiver("left-pad GHSA-aaaa-bbbb-cccc", **{field: True})) == 1


def test_a_waiver_more_than_thirty_days_out_is_refused(tmp_path: Path) -> None:
    high = FakeTools(npm=_npm(("left-pad", "GHSA-aaaa-bbbb-cccc", "high")))
    assert _run(tmp_path, high, _waiver("left-pad GHSA-aaaa-bbbb-cccc", days=31)) == 1


def test_a_waiver_that_matches_no_finding_is_refused_so_the_list_cannot_rot(tmp_path: Path) -> None:
    assert _run(tmp_path, FakeTools(), _waiver("left-pad GHSA-aaaa-bbbb-cccc")) == 1


# ── a tool that did not run is never a clean audit ────────────────────────────────────────────


@pytest.mark.parametrize(
    "tools",
    [
        pytest.param(FakeTools(export_exit=2), id="the locked export fails"),
        pytest.param(FakeTools(pip=False, pip_exit=1), id="pip-audit writes no report"),
        pytest.param(FakeTools(pip_exit=2), id="pip-audit crashes"),
        pytest.param(
            FakeTools(pip_down=1, pip_down_with="KeyError: 'info'\n"),
            id="pip-audit fails on no network error",
        ),
        pytest.param(
            FakeTools(npm={"error": {"code": "E404"}}, npm_exit=1), id="npm errors on no network"
        ),
        pytest.param(FakeTools(pip="not json"), id="pip-audit writes garbage"),
        pytest.param(FakeTools(pip={"dependencies": []}), id="pip-audit audits nothing"),
        pytest.param(FakeTools(npm="", npm_exit=1), id="npm audit prints nothing"),
        pytest.param(FakeTools(npm={"error": {"code": "ENOLOCK"}}, npm_exit=1), id="npm errors"),
        pytest.param(FakeTools(npm=CLEAN_NPM, npm_exit=127), id="npm is missing"),
        pytest.param(FakeTools(npm={"vulnerabilities": {}}), id="npm's report has no metadata"),
    ],
)
def test_a_tool_that_fails_to_run_fails_the_job(
    tmp_path: Path, tools: FakeTools, _no_summary
) -> None:
    assert _run(tmp_path, tools) == 2
    assert "The audit did not run" in _no_summary.read_text(encoding="utf-8")


# ── an advisory service that does not answer (#5848) ───────────────────────────────────────────


def _waits() -> tuple[list[float], object]:
    waited: list[float] = []
    return waited, waited.append


@pytest.mark.parametrize(
    "tools, where",
    [
        pytest.param(FakeTools(pip_down=99), "pip-audit on wheel", id="pypi"),
        pytest.param(FakeTools(npm_down=99), "npm audit on package-lock.json", id="npm"),
    ],
)
def test_an_advisory_service_that_never_answers_fails_the_job_as_infrastructure(
    tmp_path: Path, tools: FakeTools, where: str, capsys, _no_summary
) -> None:
    waited, sleep = _waits()
    assert _run(tmp_path, tools, sleep=sleep) == 2, "still red: an audit that did not run"
    assert waited == list(audit.FETCH_WAITS)
    out = capsys.readouterr().out
    assert (
        "::error title=infrastructure::the dependency audit could not reach its advisory service"
        f" ({where})\n"
    ) in out
    assert "did not run" not in out
    summary = _no_summary.read_text(encoding="utf-8")
    assert "**The audit could not reach its advisory service**, after 3 more tries" in summary
    tool = "pip_audit" if "pip" in where else "npm"
    assert sum(tool in call for call in tools.calls) == 1 + len(audit.FETCH_WAITS)


@pytest.mark.parametrize("pip", [CLEAN_PIP, VULN_PIP], ids=["clean", "a-finding"])
def test_an_advisory_service_that_answers_on_a_later_try_is_audited_as_usual(
    tmp_path: Path, pip: dict, capsys
) -> None:
    waited, sleep = _waits()
    found = _run(tmp_path, FakeTools(pip=pip, pip_down=2, npm_down=1), sleep=sleep)
    assert found == (1 if pip is VULN_PIP else 0)
    assert waited == list(audit.FETCH_WAITS)
    assert "infrastructure" not in capsys.readouterr().out


def test_the_tries_are_the_whole_run_s_not_each_tool_s(tmp_path: Path, capsys) -> None:
    """Three more tries in all, however many graphs the outage reaches: the job's bound holds."""
    waited, sleep = _waits()
    assert _run(tmp_path, FakeTools(pip_down=3, npm_down=1), sleep=sleep) == 2
    assert waited == list(audit.FETCH_WAITS)
    assert "advisory service (npm audit on package-lock.json)" in capsys.readouterr().out


def test_an_unreadable_baseline_fails_the_job(tmp_path: Path) -> None:
    assert _run(tmp_path, FakeTools(), "[[waiver]\n") == 2


# ── the tree's own baseline ───────────────────────────────────────────────────────────────────


def test_the_checked_in_baseline_is_well_formed_and_each_waiver_is_live() -> None:
    """Every waiver in the tree has all its fields and expires within 30 days of today; the
    audit itself, on the night, adds that each one still matches a finding."""
    waivers = audit.load_waivers(audit.BASELINE)
    today = datetime.datetime.now(datetime.UTC).date()
    for entry in waivers:
        assert set(audit.FIELDS) <= set(entry), entry
        assert entry["scope"] in audit.SCOPES, entry
        assert today <= entry["expires"] <= today + datetime.timedelta(days=audit.MAX_DAYS), entry


# ── the workflows run it, unmasked ────────────────────────────────────────────────────────────
