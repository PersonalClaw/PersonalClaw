"""``python -m tools.ci audit``: the dependency audit of the graphs core ships (#3918).

The command is driven end to end through ``audit.main`` with a fake runner standing in for
``uv export``, ``pip-audit`` and ``npm audit``, so each test pins the wiring, not a helper.
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from tools.ci import audit

REPO = Path(__file__).resolve().parents[1]
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
        pip: dict | str | None = None,
        npm: dict | str | None = None,
        pip_exit: int | None = None,
        npm_exit: int | None = None,
        export_exit: int = 0,
    ) -> None:
        self.pip = CLEAN_PIP if pip is None else pip
        self.npm = CLEAN_NPM if npm is None else npm
        self.pip_exit, self.npm_exit, self.export_exit = pip_exit, npm_exit, export_exit
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = list(argv)
        self.calls.append(argv)
        if argv[:2] == ["uv", "export"]:
            out = Path(argv[argv.index("--output-file") + 1])
            if self.export_exit == 0:
                out.write_text("idna==3.6\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, self.export_exit, "", "lock is stale")
        if "pip_audit" in argv:
            report = Path(argv[argv.index("-o") + 1])
            if self.pip is not False:
                text = self.pip if isinstance(self.pip, str) else json.dumps(self.pip)
                report.write_text(text, encoding="utf-8")
            vulnerable = isinstance(self.pip, dict) and any(
                d.get("vulns") for d in self.pip["dependencies"]
            )
            code = self.pip_exit if self.pip_exit is not None else int(vulnerable)
            return subprocess.CompletedProcess(argv, code, "", "pip-audit crashed")
        if argv[:2] == ["npm", "audit"]:
            text = self.npm if isinstance(self.npm, str) else json.dumps(self.npm)
            found = isinstance(self.npm, dict) and bool(self.npm.get("vulnerabilities"))
            code = self.npm_exit if self.npm_exit is not None else int(found)
            return subprocess.CompletedProcess(argv, code, text, "")
        raise AssertionError(f"the audit ran {argv}")


def _waiver(reference: str, /, *, scope: str = "npm-audit", days: int = 10, **drop: bool) -> str:
    fields = {
        "file": "uv.lock" if scope == "pip-audit" else "package-lock.json",
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


def _run(tmp_path: Path, tools: FakeTools, baseline: str = "", monkeypatch=None):
    path = tmp_path / "audit-baseline.toml"
    path.write_text(baseline, encoding="utf-8")
    argv = ["run", "--pip-audit", "py", "--work", str(tmp_path / "work"), "--baseline", str(path)]
    return audit.main(argv, run=tools, today=TODAY)


@pytest.fixture(autouse=True)
def _no_summary(monkeypatch, tmp_path: Path) -> Path:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    return summary


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


def test_each_graph_names_the_extras_its_artifact_installs() -> None:
    """The extras are read from the files that install them, so a new extra in the image or
    the desktop bundle cannot leave the audit on a smaller graph."""
    dockerfile = (REPO / "deploy/docker/Dockerfile.backend").read_text(encoding="utf-8")
    export = re.search(
        r"uv export --locked --no-emit-project --emit-index-url(.*?)-o ", dockerfile, re.S
    )
    assert export, "the gateway image's locked export is gone"
    assert (
        tuple(re.findall(r"--extra (\S+)", export.group(1))) == audit.PYTHON_GRAPHS["gateway-image"]
    )
    release = (REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")
    syncs = {
        tuple(re.findall(r"--extra (\S+)", line))
        for line in release.splitlines()
        if "uv sync --locked --no-editable" in line
    }
    assert syncs == {audit.PYTHON_GRAPHS["desktop-bundle"]}, syncs
    assert audit.PYTHON_GRAPHS["wheel"] == ()


def test_a_lock_pinned_to_a_vulnerable_release_is_reported_and_fails(
    tmp_path: Path, _no_summary
) -> None:
    """The pins come from the lock, so an older vulnerable pin is found even when a fresh
    resolution would pick a fixed release. pip-audit reports no severity, so it gates."""
    assert _run(tmp_path, FakeTools(pip=VULN_PIP)) == 1
    text = _no_summary.read_text(encoding="utf-8")
    assert "GHSA-jjg7-2v4v-x38h" in text and "**fails the run**" in text


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


def _job(workflow: str, job: str) -> dict:
    doc = yaml.safe_load((REPO / ".github/workflows" / workflow).read_text(encoding="utf-8"))
    return doc["jobs"][job]


@pytest.mark.parametrize("workflow", ["ci.yml", "breadth.yml"])
def test_the_audit_job_runs_the_command_and_nothing_masks_it(workflow: str) -> None:
    job = _job(workflow, "audit")
    assert "continue-on-error" not in job, workflow
    runs = [str(s.get("run", "")) for s in job["steps"]]
    command = [r for r in runs if "-m tools.ci audit run" in r]
    assert len(command) == 1, runs
    assert "|| true" not in command[0] and "|| :" not in command[0]
    assert not any(s.get("continue-on-error") for s in job["steps"])
    assert not any("pip_audit ||" in r or "npm audit ||" in r for r in runs)


def test_the_pull_request_audit_waits_on_tree_check_like_every_gated_job() -> None:
    job = _job("ci.yml", "audit")
    assert job["needs"] == ["tree-check"] or job["needs"] == "tree-check"
    assert job["if"] == "${{ !cancelled() && needs.tree-check.outputs.tested != 'true' }}"
