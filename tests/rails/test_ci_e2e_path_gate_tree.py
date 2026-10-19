"""The source-tree checks split from
``tests/test_ci_e2e_path_gate.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import posixpath
import sys

import local_actions
import pytest
import yaml
from ci_e2e_path_gate_shared import _ROOT
from rails_repo import REPO

sys.path.insert(0, str(REPO / "scripts"))

from ci_touches_frontend import touches_frontend  # noqa: E402

_CI = _ROOT / ".github" / "workflows" / "ci.yml"


#: The jobs whose execution the gate controls. Each must take the `needs:` AND the `if:` —
#: a `needs:` alone would only reorder them, which is the shape of a gate that does nothing.
GATED_JOBS = ("e2e",)


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
    # The local action the gated job sets its toolchain up with defines it too, as ci.yml does.
    ".github/actions/setup/action.yml",
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
        f"{path!r} was classified as unable to affect a rendered surface, so the "
        f"browser-driven `e2e` job would SKIP for a diff containing it. That is the silent failure "
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


@pytest.mark.parametrize("job", GATED_JOBS)
def test_every_local_action_a_gated_job_calls_runs_the_gates(job: str) -> None:
    """A gated job runs its own steps and those of each local action it calls, and of each one
    that action calls. A diff to any file of such an action alone runs the job, as a diff to
    ci.yml does, or a broken setup step would be waved through by the gate it broke."""
    folders = local_actions.called(_ci()["jobs"][job].get("steps") or [], _actions())
    assert folders, f"{job} calls no local action, so this check read nothing"
    skipped = [
        rel
        for folder in folders
        for rel in _files_of(folder)
        if touches_frontend([rel])[0] is not True
    ]
    assert not skipped, f"a diff to one of {skipped} alone skips {job}, which runs that action"


def _actions() -> dict:
    return local_actions.actions_at(_ROOT)


def _files_of(folder: str) -> list[str]:
    """The action's file under each name GitHub reads it from, and a file beside it that its
    steps may run."""
    return [
        posixpath.normpath(f"{folder}/{name}") for name in (*local_actions.ACTION_FILES, "run.sh")
    ]


def test_one_reaching_path_outvotes_any_number_of_ignorable_ones() -> None:
    """The verdict is OR over the diff, not a majority or a heuristic.

    The realistic shape of a frontend PR is one `.tsx` file beside a dozen docs and tests. A
    classifier that weighed them would skip exactly the diffs that matter most.
    """
    paths = list(MAY_SKIP) + ["web/src/pages/settings/MemoryPanel.tsx"]
    verdict, reaching = touches_frontend(paths)
    assert verdict is True
    assert reaching == ["web/src/pages/settings/MemoryPanel.tsx"]


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
    to the empty string, `== 'true'` is false, and the browser gate skips on a green run —
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
    # The whole expression, not a clause found in it: a substring survives a wrapper that
    # inverts it (`!(... == 'true')`), an always-false clause beside it (`&& false`), and the
    # tree-check clause's own `'true'`. A job output is always a STRING, so the verdict is
    # compared with the string 'true'.
    assert " ".join(condition.split()) == GATED_IF, (
        f"{job}'s condition is not {GATED_IF!r}: anything else can skip the browser gates on a "
        f"diff that changes the frontend. Got {condition!r}."
    )


#: The gated jobs' whole condition.
GATED_IF = (
    "${{ !cancelled() && needs.tree-check.outputs.tested != 'true' && "
    "needs.changes.outputs.frontend == 'true' }}"
)


@pytest.mark.parametrize(
    "mutant",
    [
        "needs.changes.outputs.frontend == 'false'",
        "needs.changes.outputs.frontend != 'true'",
        "needs.changes.outputs.frontend",
        "!(needs.changes.outputs.frontend == 'true')",
        "(needs.changes.outputs.frontend == 'true') == false",
        "!needs.changes.outputs.frontend == 'true'",
        "needs.changes.outputs.frontend == 'true' && false",
    ],
    ids=[
        "inverted",
        "negated",
        "bare truthiness",
        "wrapped in a not",
        "compared with false",
        "not binding tighter",
        "an always-false clause",
    ],
)
def test_the_condition_check_refuses_a_wrong_frontend_comparison(
    mutant: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The planted control: each wrong comparison, beside the tree-check clause, fails the check."""
    jobs = _ci()["jobs"]
    planted = {
        **jobs,
        "e2e": {
            **jobs["e2e"],
            "if": "${{ !cancelled() && needs.tree-check.outputs.tested != 'true' && "
            + mutant
            + " }}",
        },
    }
    monkeypatch.setattr(sys.modules[__name__], "_ci", lambda: {"jobs": planted})
    with pytest.raises(AssertionError):
        test_each_gated_job_takes_both_the_needs_and_the_condition("e2e")
