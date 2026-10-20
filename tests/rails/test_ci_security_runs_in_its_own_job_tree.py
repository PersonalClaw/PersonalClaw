"""The security corpus runs in its own job, in every tier that needs it (#3929).

No suite shard collects the corpus (``tools/ci/shards.py`` ``JOB_OWNED``), so the jobs named in
:data:`OWNERS` are the only place it runs. A check that reached a tier only through its in-suite
copy cannot then silently stop running there: each tier names its owner, the merge tier requires
``security``, and the merge verdict reads a plan without that job's success as red.

Each owner runs exactly the files ``JOB_OWNED`` gives ``ci.yml security``, and no pytest step of
one runs under a step ``if:``. Between them the owners run every Python ``requires-python``
allows, so no supported Python goes without the corpus.

A row in the shape of ``tests/rails/test_ci_contract.py``'s table: one check, a scaffold copied
from the tree that the check passes on, and planted controls that must each fail it (or, for a
positive control, leave it passing). It reads the catalogue, the two workflows and
``pyproject.toml``.
"""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from packaging.specifiers import SpecifierSet
from rails_repo import REPO
from test_each_tree_check_runs_in_one_place import named_runs

from tools.ci import catalogue, shards, verdict

_WORKFLOWS = Path(".github") / "workflows"
_CI = (_WORKFLOWS / "ci.yml").as_posix()
_BREADTH = (_WORKFLOWS / "breadth.yml").as_posix()

#: Each job that owns the corpus: its workflow, its id, and the runner family it runs on.
OWNERS: tuple[tuple[str, str, str], ...] = (
    ("ci.yml", "security", "ubuntu-"),
    ("breadth.yml", "security", "ubuntu-"),
    ("breadth.yml", "security-macos", "macos-"),
)
#: The corpus: the files the shards leave to ``ci.yml``'s ``security`` job. Each owner runs these.
CORPUS: frozenset[str] = frozenset(
    rel for rel, owner in shards.JOB_OWNED.items() if owner == "ci.yml security"
)
_PYTEST = re.compile(r"\bpytest\b")
#: A file a control writes to stand for the merge run's results, ``{job: result}``; a job left
#: out has no result. Absent (as on the real tree), every merge job succeeded.
PLANTED_RESULTS = "planted-results.json"
_HEAD = "a" * 40


def violations(root: Path) -> list[str]:
    """What breaks the rule under *root*, as sentences; empty when it holds."""
    try:
        cat = catalogue.load(
            (root / catalogue.CATALOGUE).read_text(encoding="utf-8"), catalogue.CATALOGUE
        )
    except catalogue.CatalogueError as exc:
        return [f"the catalogue is refused: {p}" for p in exc.problems]
    merge = [j.id for j in cat.tier("merge")]
    bad = [] if "security" in merge else ["the merge tier does not require security"]
    named = named_runs(root)
    pythons: set[str] = set()
    for workflow, job_id, runner in OWNERS:
        where = f"{workflow}:{job_id}"
        data = yaml.safe_load((root / _WORKFLOWS / workflow).read_text(encoding="utf-8"))
        job = (data.get("jobs") or {}).get(job_id)
        if not isinstance(job, dict):
            bad.append(f"{workflow} has no {job_id} job")
            continue
        if not str(job.get("runs-on", "")).startswith(runner):
            bad.append(f"{where} runs on {job.get('runs-on')!r}, not {runner}*")
        runs = {rel for owner, rel in named if owner == f"{workflow} {job_id}"}
        bad += [f"{where} does not run {rel}" for rel in sorted(CORPUS - runs)]
        bad += [f"{where} runs {rel}, which is not the corpus" for rel in sorted(runs - CORPUS)]
        steps = [s for s in job.get("steps") or [] if isinstance(s, dict)]
        gated = [s for s in steps if "if" in s and _PYTEST.search(str(s.get("run") or ""))]
        bad += [
            f"{where} runs pytest under if: {s['if']!r}, so a run can skip the corpus"
            for s in gated
        ]
        named_pythons = {
            str(s["with"]["python-version"])
            for s in steps
            if isinstance(s.get("with"), dict) and "python-version" in s["with"]
        }
        if not named_pythons:
            bad.append(f"{where} names no python-version")
        pythons |= named_pythons
        if workflow == "ci.yml":
            if "if" in job or job.get("needs") or gated:
                bad.append("ci.yml:security does not run on every pull request and every run")
            if job.get("permissions") != {"contents": "read"}:
                bad.append(f"ci.yml:security holds {job.get('permissions')!r}, not contents: read")
            if "secrets." in json.dumps(job):
                bad.append("ci.yml:security names a secret")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    allowed = SpecifierSet(str(project["requires-python"]))
    ran = {p.rsplit(".", 1)[0] for p in pythons}
    bad += [
        f"no corpus job runs Python 3.{minor}, which requires-python {allowed} allows"
        for minor in range(100)
        if allowed.contains(f"3.{minor}") and f"3.{minor}" not in ran
    ]
    planted = root / PLANTED_RESULTS
    results = (
        json.loads(planted.read_text(encoding="utf-8"))
        if planted.is_file()
        else {job_id: verdict.SUCCESS for job_id in merge}
    )
    identity = verdict.Identity(_HEAD)
    judged = verdict.decide(
        merge, [verdict.JobResult(k, r, identity) for k, r in results.items()], identity
    )
    bad += [f"the merge verdict is red: {r.key}: {r.kind}" for r in judged.reasons]
    return bad


def _scaffold(root: Path) -> None:
    for rel in (catalogue.CATALOGUE, _CI, _BREADTH, "pyproject.toml"):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((REPO / rel).read_text(encoding="utf-8"), encoding="utf-8")
    # The corpus's files, empty, so a named folder is read as the files under it.
    for rel in CORPUS:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("", encoding="utf-8")


@dataclass(frozen=True)
class Control:
    name: str
    plant: Callable[[Path], None]
    expect: str = ""
    fails: bool = True


def _edit(rel: str, *edits: tuple[str, str]) -> Callable[[Path], None]:
    """Plant by editing *rel*; a plant whose text is gone fails loudly instead of passing."""

    def plant(root: Path) -> None:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        for old, new in edits:
            assert old in text, f"the plant found no {old!r} in {rel}"
            text = text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8")

    return plant


def _in_job(rel: str, job_id: str, old: str, new: str) -> Callable[[Path], None]:
    """Plant by editing the first *old* inside job *job_id*'s block of *rel*, and nowhere else."""

    def plant(root: Path) -> None:
        path = root / rel
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        start = lines.index(f"  {job_id}:\n") + 1
        end = next((i for i in range(start, len(lines)) if re.match(r"  \S", lines[i])), len(lines))
        block = "".join(lines[start:end])
        assert old in block, f"the plant found no {old!r} in {rel}:{job_id}"
        lines[start:end] = [block.replace(old, new, 1)]
        path.write_text("".join(lines), encoding="utf-8")

    return plant


def _plants(*plants: Callable[[Path], None]) -> Callable[[Path], None]:
    """One control made of several edits, applied in order."""

    def plant(root: Path) -> None:
        for each in plants:
            each(root)

    return plant


def _results_without(drop: str, word: str | None) -> Callable[[Path], None]:
    """The merge run's results: every job green but *drop*, which has no result (``None``) or
    *word*."""

    def plant(root: Path) -> None:
        text = (root / catalogue.CATALOGUE).read_text(encoding="utf-8")
        merge = [j.id for j in catalogue.load(text, catalogue.CATALOGUE).tier("merge")]
        assert drop in merge, f"{drop} is not in the merge tier, so nothing would be planted"
        results = {j: verdict.SUCCESS for j in merge if j != drop}
        if word is not None:
            results[drop] = word
        (root / PLANTED_RESULTS).write_text(json.dumps(results), encoding="utf-8")

    return plant


CONTROLS = (
    Control(
        "the merge run with no security result",
        _results_without("security", None),
        "the merge verdict is red: security: missing",
    ),
    Control(
        "the merge run with security skipped",
        _results_without("security", verdict.SKIPPED),
        "the merge verdict is red: security: skipped",
    ),
    Control(
        "the merge run with security cancelled",
        _results_without("security", verdict.CANCELLED),
        "the merge verdict is red: security: cancelled",
    ),
    Control(
        "a merge run where every planned job succeeded passes",
        _results_without("audit", verdict.SUCCESS),
        fails=False,
    ),
    Control(
        "security dropped from the merge tier",
        _edit(
            catalogue.CATALOGUE,
            ('  "browse-live",\n  "security",\n', '  "browse-live",\n'),
            (
                'id = "security"\ntiers = ["change", "merge", "breadth"]',
                'id = "security"\ntiers = ["breadth"]',
            ),
        ),
        "the merge tier lacks security",
    ),
    Control(
        "ci.yml's security job gated again",
        _edit(_CI, ("  security:\n", "  security:\n    if: ${{ github.event_name == 'x' }}\n")),
        "does not run on every pull request",
    ),
    Control(
        "ci.yml's security job waiting on tree-check",
        _edit(_CI, ("  security:\n", "  security:\n    needs: [tree-check]\n")),
        "does not run on every pull request",
    ),
    Control(
        "ci.yml's corpus step gated to pushes",
        _in_job(
            _CI,
            "security",
            "      - name: pytest (adversarial scanner corpus)\n",
            "      - name: pytest (adversarial scanner corpus)\n"
            "        if: ${{ github.event_name == 'push' }}\n",
        ),
        "does not run on every pull request",
    ),
    Control(
        "breadth's macOS corpus step gated",
        _in_job(
            _BREADTH,
            "security-macos",
            "      - name: pytest (adversarial scanner corpus)\n",
            "      - name: pytest (adversarial scanner corpus)\n"
            "        if: ${{ github.event_name == 'workflow_dispatch' }}\n",
        ),
        "breadth.yml:security-macos runs pytest under if:",
    ),
    Control(
        "ci.yml's security job given another read scope",
        _edit(
            _CI,
            (
                "    permissions:\n      contents: read\n    # No PERSONALCLAW_HOME",
                "    permissions:\n      contents: read\n      actions: read\n"
                "    # No PERSONALCLAW_HOME",
            ),
        ),
        "not contents: read",
    ),
    Control(
        "ci.yml's security job given a write",
        _edit(
            _CI,
            (
                "    permissions:\n      contents: read\n    # No PERSONALCLAW_HOME",
                "    permissions:\n      contents: write\n    # No PERSONALCLAW_HOME",
            ),
        ),
        "not contents: read",
    ),
    Control(
        "ci.yml's security job no longer running tests/security",
        _edit(
            _CI,
            (
                "          --junitxml=reports/junit-security-corpus.xml\n"
                "          tests/security\n",
                "          --junitxml=reports/junit-security-corpus.xml\n",
            ),
        ),
        "ci.yml:security does not run tests/security",
    ),
    Control(
        "breadth's macOS leg gone",
        _edit(_BREADTH, ("  security-macos:\n", "  security-macos-renamed:\n")),
        "breadth.yml has no security-macos job",
    ),
    Control(
        "breadth's Linux leg moved to macOS",
        _in_job(_BREADTH, "security", "    runs-on: ubuntu-24.04\n", "    runs-on: macos-26\n"),
        "breadth.yml:security runs on",
    ),
    Control(
        "breadth's macOS leg narrowed to one file, in its workflow and its catalogue entry",
        _plants(
            _in_job(
                _BREADTH,
                "security-macos",
                "          tests/security\n",
                "          tests/security/test_app_signature.py\n",
            ),
            _edit(
                catalogue.CATALOGUE,
                (
                    '"--junitxml=reports/junit-security-corpus-macos.xml", "tests/security",',
                    '"--junitxml=reports/junit-security-corpus-macos.xml",'
                    ' "tests/security/test_app_signature.py",',
                ),
            ),
        ),
        "breadth.yml:security-macos does not run tests/security/test_scanner_adversarial.py",
    ),
    Control(
        "breadth's macOS leg running a file the shards collect too",
        _in_job(
            _BREADTH,
            "security-macos",
            "          tests/test_supply_chain_gates.py\n",
            "          tests/test_supply_chain_gates.py\n          tests/test_ci_shards.py\n",
        ),
        "breadth.yml:security-macos runs tests/test_ci_shards.py, which is not the corpus",
    ),
    Control(
        "breadth's macOS leg back on Python 3.12, so no corpus job runs 3.13",
        _plants(
            _in_job(_BREADTH, "security-macos", 'PY: "3.13.16"', 'PY: "3.12.15"'),
            _in_job(
                _BREADTH,
                "security-macos",
                'python-version: "3.13.16"',
                'python-version: "3.12.15"',
            ),
        ),
        "no corpus job runs Python 3.13",
    ),
)


def test_the_scaffold_holds_the_rule(tmp_path: Path) -> None:
    _scaffold(tmp_path / "scaffold")
    assert violations(tmp_path / "scaffold") == []


@pytest.mark.parametrize("control", CONTROLS, ids=lambda c: c.name)
def test_each_planted_violation_is_caught(control: Control, tmp_path: Path) -> None:
    _scaffold(tmp_path / "scaffold")
    root = tmp_path / "control"
    shutil.copytree(tmp_path / "scaffold", root)
    control.plant(root)
    found = violations(root)
    if control.fails:
        assert found, f"the planted violation went unseen: {control.name}"
        assert any(control.expect in v for v in found), "\n".join(found)
    else:
        assert found == [], "\n".join(found)


def test_the_tree_holds_the_rule() -> None:
    found = violations(REPO)
    assert found == [], "\n".join(found)
