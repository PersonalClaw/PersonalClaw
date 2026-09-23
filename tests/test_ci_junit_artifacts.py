"""Every CI pytest step writes a JUnit report, and a RED run still publishes it.

Measured gap (2026-09-17): when the sharded ``test`` job failed, the only signal a
reviewer got was the annotation ``Process completed with exit code 1``. No artifact
named the failing case, so deciding flake-vs-genuine cost either a full log read (which
the #2720 truncation can defeat outright — the summary is the part that gets eaten) or a
blind ~40-minute re-run. That tax was paid once per red, forever.

The fix is two lines of workflow per job: ``--junitxml=reports/junit-….xml`` on the
pytest invocation and an ``actions/upload-artifact`` step gated ``always()`` so the
report survives the failure that makes it worth having. The rails below keep both halves
honest, because either one alone is silently useless:

* a ``--junitxml`` nobody uploads writes a report onto a runner that is then discarded;
* an upload gated on ``success()`` publishes the report only on the runs that need it least;
* an artifact ``name`` without the matrix coordinate makes N shards collide into one
  upload, and GitHub keeps whichever finished last — so the shard you want is the one
  you cannot have.

Parsed from the workflow source, not executed: GitHub Actions is not available to the
suite, and the shape asserted here ("this step's command carries this flag", "this job
has an upload step") is carried by the text. Same convention as
``tests/test_ci_tier_enforcement.py`` and ``tests/test_gate_aggregate_runs_in_ci.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import real_home_guard

_REPO = Path(__file__).resolve().parents[1]
_WORKFLOWS = _REPO / ".github" / "workflows"
_CI = _WORKFLOWS / "ci.yml"
_FULL = _WORKFLOWS / "full.yml"

#: Job ids old enough in each workflow that their absence means the parser broke rather
#: than that CI changed. The vacuity floor for :func:`jobs`.
_LONG_STANDING = {
    "ci.yml": frozenset({"lint", "test-shard", "test", "web", "rails", "harness", "client"}),
    "full.yml": frozenset({"matrix-shard", "matrix", "audit", "security-corpus", "coverage"}),
}

#: The directory every CI JUnit report is written under. Asserted rather than free-form so
#: ``.gitignore`` can cover the class with one entry instead of one per job.
_REPORT_DIR = "reports/"


# ── parsers (text in, answer out — so the vacuity tests below can drive them) ──────────


def jobs(text: str) -> dict[str, str]:
    """Job id → the block of ``text`` belonging to it.

    A job id is the only key at two-space indent in a workflow: a job's own keys sit at
    four and steps deeper still, so this needs no YAML parser (PyYAML is not a declared
    test dependency).
    """
    found: dict[str, list[str]] = {}
    current: str | None = None
    in_jobs = False
    for line in text.splitlines():
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line.strip() and not line.startswith(" ") and not line.startswith("#"):
            break  # back to a top-level key: the jobs mapping ended
        header = re.match(r"^ {2}([A-Za-z][\w-]*):\s*(#.*)?$", line)
        if header:
            current = header.group(1)
            found[current] = []
            continue
        if current is not None:
            found[current].append(line)
    return {k: "\n".join(v) for k, v in found.items()}


def steps(block: str) -> list[str]:
    """A job block split into its steps — each ``- name:``/``- uses:`` entry and its keys.

    Six spaces is the step-list indent in both workflows; a ``run:`` block scalar's body
    sits at ten or more, so no shell line can be mistaken for a step boundary.
    """
    out: list[str] = []
    current: list[str] | None = None
    for line in block.splitlines():
        if re.match(r"^ {6}-\s", line):
            if current is not None:
                out.append("\n".join(current))
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        out.append("\n".join(current))
    return out


def commands(step: str) -> list[str]:
    """The shell commands a step runs, one per element, comments dropped.

    The three ``run:`` forms are normalized, because which one a step uses is a legibility
    choice and a rail that cared would break on a reflow:

    * ``run: cmd`` → one command;
    * ``run: |`` (literal) → one per line, joining ``\\``-continuations;
    * ``run: >`` (folded) → the whole payload joined into ONE command, which is what the
      shell receives. ``security-corpus`` spells its invocation this way, so a per-line
      scan would read the flag as living on a different command from the ``pytest``.
    """
    lines = step.splitlines()
    for i, raw in enumerate(lines):
        head = re.match(r"^(\s*)-?\s*run:\s*(.*?)\s*$", raw)
        if not head:
            continue
        indent, inline = len(head.group(1)), head.group(2)
        if inline and inline not in ("|", ">", "|-", ">-", "|+", ">+"):
            return [inline]
        folded = inline.startswith(">")
        body: list[str] = []
        for follow in lines[i + 1 :]:
            if not follow.strip():
                continue
            if len(follow) - len(follow.lstrip()) <= indent:
                break
            text = follow.strip()
            if not text.startswith("#"):
                body.append(text)
        if folded:
            return [" ".join(body)] if body else []
        joined: list[str] = []
        for text in body:
            if joined and joined[-1].endswith("\\"):
                joined[-1] = joined[-1][:-1].rstrip() + " " + text
            else:
                joined.append(text)
        return joined
    return []


def pytest_commands(step: str) -> list[str]:
    """Commands in ``step`` that INVOKE pytest — not ones that merely say the word."""
    return [c for c in commands(step) if re.search(r"(?:^|[\s/])(?:-m\s+)?pytest(?:\s|$)", c)]


def key(step: str, name: str) -> str | None:
    """The value of a step's OWN key (``if``, ``uses``, the display ``name``, …)."""
    hit = re.search(rf"^\s*-?\s*{re.escape(name)}:\s*(.*?)\s*$", step, flags=re.MULTILINE)
    return hit.group(1) if hit else None


def with_key(step: str, name: str) -> str | None:
    """The value of a key inside a step's ``with:`` mapping.

    Separate from :func:`key` because an upload step has TWO keys called ``name``: its own
    display name and the artifact name under ``with:``. MEASURED while mutating this rail —
    a scan that took the first ``name:`` read the display name (which happens to interpolate
    ``${{ matrix.shard }}``), so replacing the ARTIFACT name with a fixed string left the
    collision rail below green. A rail that reads the wrong key is not a rail.
    """
    lines = step.splitlines()
    for i, raw in enumerate(lines):
        head = re.match(r"^(\s*)with:\s*$", raw)
        if not head:
            continue
        base = len(head.group(1))
        for follow in lines[i + 1 :]:
            if not follow.strip():
                continue
            if len(follow) - len(follow.lstrip()) <= base:
                break
            hit = re.match(rf"^\s*{re.escape(name)}:\s*(.*?)\s*$", follow)
            if hit:
                return hit.group(1)
    return None


def junit_uploads(block: str) -> list[str]:
    """Steps in a job that upload a JUnit report."""
    return [
        s
        for s in steps(block)
        if "actions/upload-artifact" in s and "junit" in (with_key(s, "path") or "")
    ]


# ── fixtures ──────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def workflows() -> dict[str, dict[str, str]]:
    parsed = {}
    for path in (_CI, _FULL):
        assert path.is_file(), f"{path} moved — re-point this rail"
        parsed[path.name] = jobs(path.read_text(encoding="utf-8"))
    return parsed


def _pytest_jobs(parsed: dict[str, str]) -> dict[str, str]:
    return {
        name: block
        for name, block in parsed.items()
        if any(pytest_commands(s) for s in steps(block))
    }


# ── vacuity floors: each parser must be shown to find the real thing ──────────────────


def test_the_job_scan_is_not_vacuous(workflows: dict[str, dict[str, str]]) -> None:
    """If :func:`jobs` returned nothing, every rail below would pass on an empty set."""
    for filename, expected in _LONG_STANDING.items():
        missing = expected - set(workflows[filename])
        assert not missing, f"the job scan lost long-standing {filename} jobs {sorted(missing)}"


def test_the_pytest_step_scan_finds_every_known_leg(workflows: dict[str, dict[str, str]]) -> None:
    """The rails are about pytest steps, so a scan that found none would be self-satisfying.

    Named by JOB rather than by count: a new pytest job should extend this list (and pick up
    the rails with it), not silently shrink what they cover.
    """
    ci = set(_pytest_jobs(workflows["ci.yml"]))
    full = set(_pytest_jobs(workflows["full.yml"]))
    assert ci >= {"lint", "test-shard", "browse-live", "rails", "client"}, sorted(ci)
    assert full >= {"matrix-shard", "security-corpus", "coverage"}, sorted(full)


def test_the_command_parser_normalizes_all_three_run_forms() -> None:
    """``run: cmd`` · ``run: |`` · ``run: >`` — and the folded form must join, not split.

    The load-bearing case is the folded one: ``security-corpus`` puts its ``--junitxml`` on a
    different SOURCE line from its ``pytest``, and a parser that kept them apart would call
    that job unflagged (a false red) while a parser that joined every form would call a
    genuinely-unflagged literal block flagged (a false green). Both directions, once.
    """
    inline = "      - name: x\n        run: uv run pytest -n0 --junitxml=reports/junit-x.xml"
    assert commands(inline) == ["uv run pytest -n0 --junitxml=reports/junit-x.xml"]

    literal = "      - name: x\n        run: |\n          echo one\n          uv run pytest -n0"
    assert commands(literal) == ["echo one", "uv run pytest -n0"]

    folded = "      - name: x\n        run: >\n          uv run pytest -n0\n          tests/a.py"
    assert commands(folded) == ["uv run pytest -n0 tests/a.py"]

    continued = "      - name: x\n        run: |\n          uv run pytest \\\n            -n0"
    assert commands(continued) == ["uv run pytest -n0"]

    commented = "      - name: x\n        run: |\n          # uv run pytest\n          echo hi"
    assert pytest_commands(commented) == [], "a commented-out invocation is not an invocation"

    assert pytest_commands("      - name: pytest things\n        run: echo pytest-ish") == []


def test_an_unflagged_or_success_gated_job_is_detected() -> None:
    """Each predicate must be able to answer NO, or none of them is a rail.

    Three synthetic jobs, one per way the wiring rots: no ``--junitxml``, no upload step,
    and an upload gated on the default ``success()`` (the subtle one — it looks wired and
    publishes nothing on the only runs that matter).
    """
    good = """\
jobs:
  ok:
    steps:
      - name: pytest
        run: uv run pytest --junitxml=reports/junit-ok.xml
      - name: Upload JUnit results
        if: always()
        uses: actions/upload-artifact@v7
        with:
          path: reports/junit-ok.xml
"""
    block = jobs(good)["ok"]
    assert pytest_commands(steps(block)[0]) and "--junitxml=" in pytest_commands(steps(block)[0])[0]
    assert junit_uploads(block) and key(junit_uploads(block)[0], "if") == "always()"

    unflagged = jobs(good.replace(" --junitxml=reports/junit-ok.xml", "", 1))["ok"]
    assert "--junitxml=" not in pytest_commands(steps(unflagged)[0])[0]

    no_upload = jobs("\n".join(good.splitlines()[:6]) + "\n")["ok"]
    assert junit_uploads(no_upload) == []

    success_gated = jobs(good.replace("        if: always()\n", "", 1))["ok"]
    assert key(junit_uploads(success_gated)[0], "if") is None


def test_the_artifact_name_is_read_from_with_not_from_the_display_name() -> None:
    """The bug this rail shipped with, kept as a case.

    MEASURED: mutating ci.yml's artifact ``name`` to a fixed string left
    :func:`test_a_matrix_job_names_its_artifact_per_leg` GREEN, because the first ``name:``
    in an upload step is the step's DISPLAY name — which interpolates the shard index for
    legibility. So the rail was reading a key that is free to be right while the one that
    matters is wrong. Both keys are asserted here, in the shape that fooled it.
    """
    step = """\
      - name: Upload JUnit results (shard ${{ matrix.shard }})
        if: always()
        uses: actions/upload-artifact@v7
        with:
          name: junit-test-shard
          path: reports/junit-test-shard.xml
"""
    assert key(step, "name") == "Upload JUnit results (shard ${{ matrix.shard }})"
    assert with_key(step, "name") == "junit-test-shard", "with: name must win over the label"
    assert with_key(step, "path") == "reports/junit-test-shard.xml"
    assert with_key(step, "retention-days") is None, "an absent with: key is None, not ''"


# ── the rails ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("filename", ["ci.yml", "full.yml"])
def test_every_pytest_invocation_writes_a_junit_report(
    workflows: dict[str, dict[str, str]], filename: str
) -> None:
    """A pytest step with no ``--junitxml`` has nothing to publish."""
    for job, block in _pytest_jobs(workflows[filename]).items():
        for step in steps(block):
            for command in pytest_commands(step):
                assert f"--junitxml={_REPORT_DIR}" in command, (
                    f"{filename} job `{job}` invokes pytest without "
                    f"`--junitxml={_REPORT_DIR}…`, so a red run publishes no report and "
                    f"classifying it costs a full log read or a blind re-run: {command}"
                )


@pytest.mark.parametrize("filename", ["ci.yml", "full.yml"])
def test_every_pytest_job_uploads_its_report_even_when_red(
    workflows: dict[str, dict[str, str]], filename: str
) -> None:
    """And uploads it on FAILURE — a ``success()``-gated report is the wrong half."""
    for job, block in _pytest_jobs(workflows[filename]).items():
        uploads = junit_uploads(block)
        assert uploads, (
            f"{filename} job `{job}` writes a JUnit report but no step uploads it, so the "
            "XML dies with the runner and the red is as opaque as before."
        )
        for upload in uploads:
            condition = key(upload, "if") or "success()"
            assert re.search(r"\balways\(\)|\bfailure\(\)|!\s*cancelled\(\)", condition), (
                f"{filename} job `{job}` uploads its JUnit report under `if: {condition}`. "
                "The default is success(), which publishes the report only on the runs that "
                "do not need it. Use `if: always()`."
            )


@pytest.mark.parametrize("filename", ["ci.yml", "full.yml"])
def test_a_matrix_job_names_its_artifact_per_leg(
    workflows: dict[str, dict[str, str]], filename: str
) -> None:
    """Artifact names are unique per run, so N legs sharing one name collapse to one upload.

    ci.yml's ``test-shard`` runs 4 legs and full.yml's ``matrix-shard`` runs 24; a fixed name
    would keep whichever finished last, which is reliably not the one that failed.
    """
    matrixed = {
        job: block
        for job, block in _pytest_jobs(workflows[filename]).items()
        if re.search(r"^ {6}matrix:\s*$", block, flags=re.MULTILINE)
    }
    # Vacuity floor, per file: the sharded job is named, not counted, so a `matrix:` indent
    # change reds HERE (parser broke) rather than quietly emptying the rail below.
    expected = {"ci.yml": "test-shard", "full.yml": "matrix-shard"}[filename]
    assert expected in matrixed, (
        f"the matrix scan did not find {filename}'s `{expected}` job (found: "
        f"{sorted(matrixed)}) — the parser broke, not the workflow."
    )
    for job, block in matrixed.items():
        for upload in junit_uploads(block):
            name = with_key(upload, "name") or ""
            assert "${{ matrix." in name, (
                f"{filename} job `{job}` uploads every matrix leg's JUnit report as "
                f"{name!r}. Without a `${{{{ matrix.… }}}}` coordinate the legs collide and "
                "only one report survives."
            )


def test_the_real_home_rail_report_is_uploaded_per_shard(
    workflows: dict[str, dict[str, str]],
) -> None:
    """The one red the JUnit report structurally CANNOT explain still reaches an artifact.

    The real-home rail fails a run from ``pytest_sessionfinish`` by assigning
    ``session.exitstatus`` — no testcase fails, so the XML reads ``failures=0`` and the
    shard surfaces as a bare process-level exit naming nothing. MEASURED: that signature
    cost three shifts of guessing on one PR, because the offending paths lived only on
    STDOUT, where the #2720 truncation class can eat them (#3386).

    The expected path is read from :data:`real_home_guard.REPORT_RELPATH` rather than
    spelled here, so moving the writer without moving the upload reds this rail instead
    of silently publishing a file that no longer exists.
    """
    block = workflows["ci.yml"]["test-shard"]
    wanted = real_home_guard.REPORT_RELPATH
    assert wanted.startswith(_REPORT_DIR), (
        f"the rail report is written to {wanted!r}, outside {_REPORT_DIR!r} — the only "
        "directory .gitignore covers and CI uploads from."
    )
    uploads = [
        s
        for s in steps(block)
        if "actions/upload-artifact" in s and (with_key(s, "path") or "") == wanted
    ]
    assert uploads, (
        f"ci.yml job `test-shard` runs the whole suite but no step uploads {wanted}, so a "
        "rail-caused red is unattributable from any artifact."
    )
    for upload in uploads:
        condition = key(upload, "if") or "success()"
        assert re.search(r"\balways\(\)|\bfailure\(\)|!\s*cancelled\(\)", condition), (
            f"the rail report is uploaded under `if: {condition}`, which skips exactly the "
            "failing runs it exists for. Use `if: always()`."
        )
        name = with_key(upload, "name") or ""
        assert "${{ matrix." in name, (
            f"all four shards upload the rail report as {name!r}; without a matrix "
            "coordinate they collide and only the last leg to finish survives."
        )


def test_the_report_directory_is_gitignored() -> None:
    """A local copy of a CI invocation must not leave a report a ``git add -A`` would sweep."""
    ignored = (_REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert f"/{_REPORT_DIR}" in [line.strip() for line in ignored], (
        f"`/{_REPORT_DIR}` is not in .gitignore, so running a CI pytest command locally "
        "leaves an untracked report at the repo root."
    )
