"""Every CI pytest step writes a JUnit report, and a RED run still publishes it.

Measured gap: when the sharded ``test`` job failed, the only signal a
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
``tests/rails/test_ci_tier_enforcement.py`` and ``tests/test_gate_aggregate_runs_in_ci.py``.
"""

from __future__ import annotations

from ci_junit_artifacts_shared import (
    _REPO,
    _REPORT_DIR,
    commands,
    jobs,
    junit_uploads,
    key,
    pytest_commands,
    steps,
    with_key,
)

# ── parsers (text in, answer out — so the vacuity tests below can drive them) ──────────


# ── fixtures ──────────────────────────────────────────────────────────────────────────


# ── vacuity floors: each parser must be shown to find the real thing ──────────────────


def test_the_command_parser_normalizes_all_three_run_forms() -> None:
    """``run: cmd`` · ``run: |`` · ``run: >`` — and the folded form must join, not split.

    The load-bearing case is the folded one: ``security`` puts its ``--junitxml`` on a
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

    Mutating ci.yml's artifact ``name`` to a fixed string left
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


def test_the_report_directory_is_gitignored() -> None:
    """A local copy of a CI invocation must not leave a report a ``git add -A`` would sweep."""
    ignored = (_REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert f"/{_REPORT_DIR}" in [line.strip() for line in ignored], (
        f"`/{_REPORT_DIR}` is not in .gitignore, so running a CI pytest command locally "
        "leaves an untracked report at the repo root."
    )
