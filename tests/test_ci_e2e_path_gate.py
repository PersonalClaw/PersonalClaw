"""The browser gates' admission decision, railed in both directions.

`ci.yml`'s `changes` job decides whether `e2e-a11y`, `e2e-walkthrough` and `e2e-pwa` run at all.
A gate like that has two opposite failure modes and only one of them is loud:

* **Too eager** — it runs when it did not have to. Costs minutes. Visible in every run's timing.
* **Too lazy** — it skips a diff that CAN break a rendered surface. Costs a missed regression,
  reports a green check, and nothing ever says so.

So the asymmetry is encoded as tests rather than as a comment. `scripts/ci_touches_frontend.py`
is an IGNORE-list whose verdict defaults to `True`; the cases below pin the entries that may be
ignored, pin the ones that must never be, and assert the classifier is not vacuous in either
direction. The last group asserts the workflow actually WIRES it — a classifier no job calls is
the dead-helper failure class this repo has hit repeatedly, and it would leave the ignore-list
looking authoritative while deciding nothing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ci_touches_frontend import IGNORABLE, touches_frontend  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_SCRIPT = _ROOT / "scripts" / "ci_touches_frontend.py"

#: The jobs whose execution the gate controls. Each must take the `needs:` AND the `if:` —
#: a `needs:` alone would only reorder them, which is the shape of a gate that does nothing.
GATED_JOBS = ("e2e-a11y", "e2e-walkthrough", "e2e-pwa")

#: Paths that MUST keep paying the gate. Each is a real reachability claim, not a guess:
#: see the script's docstring and web/playwright.config.ts's header for the derivations.
MUST_RUN = (
    "web/src/pages/settings/MemoryPanel.tsx",
    "web/e2e/a11y.spec.ts",
    "web/e2e/routes.ts",
    "web/playwright.config.ts",
    "web/package.json",
    "package.json",
    "package-lock.json",
    # The gateway the harness boots. Sixteen of eighteen routes are `needsData: true`, so API
    # responses ARE scanned DOM; without the gateway every route renders onboarding instead.
    "src/personalclaw/dashboard/app.py",
    "src/personalclaw/llm/scripted.py",
    "src/personalclaw/config/loader.py",
    # Model input, not prose — deliberately outside the root-level `*.md` exemption.
    "src/personalclaw/config/prompt_snippets/persona-claw-arcade.md",
    # The other two npm workspace members: they share the single root lockfile, so a dependency
    # move in either can change what the SPA is built against (npm/cli#4828).
    "desktop/main.js",
    "mobile/package.json",
    # The file that DEFINES the gated jobs. If editing it could skip them, a broken job
    # definition would be waved through by the very gate it broke.
    ".github/workflows/ci.yml",
    # Default-deny: a path in no rule at all must run, which is what makes a new top-level
    # directory safe by construction rather than safe by someone remembering this file.
    "an-unheard-of-top-level-dir/thing.ts",
    "Makefile",
    "pyproject.toml",
)

#: Paths that may be ignored, each mirroring an IGNORABLE entry.
MAY_SKIP = (
    "docs/architecture/provider-boundary.md",
    "docs/vision.md",
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "tests/test_config_roundtrip.py",
    "tests/security/test_corpus.py",
    "harness/scan.py",
    "packages/personalclaw-client-py/src/personalclaw_client/__init__.py",
    ".github/dependabot.yml",
    ".github/workflows/release.yml",
    ".github/ISSUE_TEMPLATE/bug.md",
)


@pytest.mark.parametrize("path", MUST_RUN)
def test_a_reaching_path_forces_the_gates_to_run(path: str) -> None:
    verdict, reaching = touches_frontend([path])
    assert verdict is True, (
        f"{path!r} was classified as unable to affect a rendered surface, so the three "
        f"browser-driven jobs would SKIP for a diff containing it. That is the silent failure "
        f"direction: a missed a11y/keyboard/service-worker regression reporting a green check. "
        f"If this is deliberate, the reason belongs in scripts/ci_touches_frontend.py's "
        f"IGNORABLE table AND in this list."
    )
    assert reaching == [path]


@pytest.mark.parametrize("path", MAY_SKIP)
def test_an_out_of_reach_path_alone_skips_the_gates(path: str) -> None:
    verdict, reaching = touches_frontend([path])
    assert verdict is False, (
        f"{path!r} is declared out of reach in IGNORABLE but the classifier still runs the "
        f"gates for it. Harmless, but it means the saving this gate exists for is not being "
        f"taken — and that the table and the code disagree."
    )
    assert reaching == []


def test_one_reaching_path_outvotes_any_number_of_ignorable_ones() -> None:
    """The verdict is OR over the diff, not a majority or a heuristic.

    The realistic shape of a frontend PR is one `.tsx` file beside a dozen docs and tests. A
    classifier that weighed them would skip exactly the diffs that matter most.
    """
    paths = list(MAY_SKIP) + ["web/src/pages/settings/MemoryPanel.tsx"]
    verdict, reaching = touches_frontend(paths)
    assert verdict is True
    assert reaching == ["web/src/pages/settings/MemoryPanel.tsx"]


def test_an_unenumerated_diff_runs_the_gates() -> None:
    """No paths means an UNKNOWN diff, not an empty one.

    The compare API returning nothing, a base ref failing to resolve, or a fork event shape
    nobody anticipated all land here. Skipping would convert the absence of an answer into a
    passing one — the failure class this repo's rails exist to catch.
    """
    assert touches_frontend([])[0] is True
    assert touches_frontend(["", "   "])[0] is True


def test_the_classifier_is_not_vacuous_in_either_direction() -> None:
    """Both answers must be reachable, or the parametrized cases above prove nothing.

    A classifier hardwired to `True` passes every MUST_RUN case; one hardwired to `False`
    passes every MAY_SKIP case. Only asserting that both answers occur rules out both.
    """
    assert touches_frontend(["web/src/App.tsx"])[0] is True
    assert touches_frontend(["docs/vision.md"])[0] is False


def test_every_ignorable_entry_carries_a_real_reason() -> None:
    """ "Later" is not a reason — the text must say what makes the path unreachable."""
    thin = [what for what, why in IGNORABLE if len(why) < 60]
    assert not thin, f"these ignore-list entries state no mechanical reason: {thin}"


def test_the_e2e_specs_own_directory_can_never_be_ignored() -> None:
    """The gate must not be able to hide the specs it gates.

    `tests/test_e2e_specs_are_executed.py` asserts each spec is NAMED on a workflow run line.
    It cannot see a conditional that stops the job running, so this is the other half of that
    rail: editing a spec always re-runs it.
    """
    specs = sorted((_ROOT / "web" / "e2e").glob("*.spec.ts"))
    assert specs, "no e2e specs found — this sweep would pass vacuously"
    for spec in specs:
        rel = spec.relative_to(_ROOT).as_posix()
        assert touches_frontend([rel])[0] is True, f"{rel} would not re-run its own gate"


# ── The wiring. A classifier no workflow calls decides nothing. ──────────────────────────────


def _ci() -> dict:
    return yaml.safe_load(_CI.read_text(encoding="utf-8"))


def _changes_shell() -> str:
    """The `changes` job's shell, as WRITTEN.

    Read off the parsed `run:` scalars rather than `yaml.safe_dump(job)`: dumping re-quotes the
    block and escapes the very `verdict="frontend=true"` the fail-open test below looks for, so
    that assertion passed vacuously in reverse — it failed on a correct workflow.
    """
    return "\n".join(
        step["run"]
        for step in _ci()["jobs"]["changes"]["steps"]
        if isinstance(step.get("run"), str)
    )


def test_the_changes_job_exists_and_invokes_the_classifier() -> None:
    jobs = _ci()["jobs"]
    assert "changes" in jobs, "ci.yml has no `changes` job, so nothing computes the verdict"
    body = _changes_shell()
    assert "scripts/ci_touches_frontend.py" in body, (
        "the `changes` job does not run scripts/ci_touches_frontend.py — the ignore-list would "
        "be documentation of a decision nobody makes"
    )
    assert jobs["changes"]["outputs"]["frontend"], "`changes` publishes no `frontend` output"


def test_a_crashed_classifier_runs_the_gates_rather_than_skipping_them() -> None:
    """The workflow must VALIDATE the verdict before publishing it.

    Appending the script's stdout straight to `$GITHUB_OUTPUT` looks equivalent and fails in the
    silent direction: if the script raises, no `frontend` key is written, the expression resolves
    to the empty string, `== 'true'` is false, and all three browser gates skip on a green run —
    a crashed classifier disabling the gates it exists to schedule. So the step must coerce any
    non-legal answer to `true`.
    """
    body = _changes_shell()
    assert "frontend=true|frontend=false" in body, (
        "the `changes` job does not whitelist the two legal verdicts before publishing, so a "
        "crashed or missing classifier would resolve to the empty string and SKIP every gate"
    )
    assert 'verdict="frontend=true"' in body, (
        "the `changes` job has no fail-open default. An inconclusive classifier must run the "
        "gates, never silently skip them."
    )


@pytest.mark.parametrize("job", GATED_JOBS)
def test_each_gated_job_takes_both_the_needs_and_the_condition(job: str) -> None:
    spec = _ci()["jobs"][job]
    assert "changes" in (spec.get("needs") or []), f"{job} does not `needs: [changes]`"
    condition = spec.get("if") or ""
    assert "needs.changes.outputs.frontend" in condition, (
        f"{job} depends on `changes` but does not READ its verdict, so the gate only reorders "
        f"the run. Condition was {condition!r}."
    )
    assert "'true'" in condition, (
        f"{job}'s condition does not compare the verdict to the string 'true'. A job output is "
        f"always a STRING, so a bare truthiness check is true for 'false' too. Got {condition!r}."
    )


def test_the_script_runs_as_a_cli_and_prints_a_github_output_line() -> None:
    """The workflow appends stdout straight to $GITHUB_OUTPUT, so its shape is a contract."""
    for paths, want in ((b"docs/vision.md\n", "frontend=false"), (b"web/x.tsx\n", "frontend=true")):
        done = subprocess.run(
            [sys.executable, str(_SCRIPT)], input=paths, capture_output=True, check=True
        )
        assert done.stdout.decode().strip() == want
        assert done.stderr.decode().strip(), "no human-readable verdict was written for the summary"
