"""The real catalogue's plans (tools/ci/plan.py over tools/ci/catalogue.toml).

It reads the tree's catalogue, its overrides (tools/ci/areas.toml), ci.yml and scripts, so it is
a rail: it runs once per CI run, in the rails job. tests/test_ci_plan.py holds the planner itself
on a planted catalogue.
"""

from __future__ import annotations

import ast
import posixpath
import textwrap

import local_actions
import pytest
import yaml
from rails_repo import REPO

from tools.ci import catalogue
from tools.ci import plan as planner

_CAT = catalogue.read(REPO)
_AREAS = catalogue.read_areas(REPO, _CAT)
_MERGE = {j.id for j in _CAT.tier("merge")}


def _plan(*paths: str, tier: str = "merge") -> planner.Plan:
    return planner.plan(_CAT, tier, list(paths), overrides=_AREAS, root=REPO)


# ── the browser run keeps the frontend classifier's answers ──────────────────────────────────

#: Paths that must run e2e: each a reachability claim the old classifier's rail held.
MUST_RUN_E2E = (
    "web/src/pages/settings/MemoryPanel.tsx",
    "web/e2e/a11y.spec.ts",
    "web/e2e/routes.ts",
    "web/playwright.config.ts",
    "web/package.json",
    "package.json",
    "package-lock.json",
    # The gateway the browser run boots: API responses are scanned DOM.
    "src/personalclaw/dashboard/app.py",
    "src/personalclaw/llm/scripted.py",
    "src/personalclaw/config/loader.py",
    # Model input, not prose: outside the root-level *.md exclusion.
    "src/personalclaw/config/prompt_snippets/persona-claw-arcade.md",
    # The other npm workspace members share the root lockfile.
    "desktop/main.js",
    "mobile/package.json",
    # The workflows, which define the jobs and when they run.
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
    ".github/workflows/breadth.yml",
    # The local action the jobs set their toolchain up with, which defines them as ci.yml does.
    ".github/actions/setup/action.yml",
    # A path in no rule at all.
    "an-unheard-of-top-level-dir/thing.ts",
    "Makefile",
    "pyproject.toml",
)

#: Paths e2e may skip, each under one of its declared exclusions.
MAY_SKIP_E2E = (
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
    ".github/ISSUE_TEMPLATE/bug.md",
    ".github/CODEOWNERS",
)


@pytest.mark.parametrize("path", MUST_RUN_E2E)
def test_a_path_that_can_reach_the_browser_run_plans_e2e(path: str) -> None:
    assert "e2e" in _plan(path).jobs, path


@pytest.mark.parametrize("path", MAY_SKIP_E2E)
def test_a_path_that_cannot_reach_the_browser_run_leaves_e2e_out(path: str) -> None:
    p = _plan(path)
    assert not p.full and "e2e" not in p.jobs, (path, p.full_reasons)


def test_one_reaching_path_outvotes_any_number_of_skippable_ones() -> None:
    assert "e2e" in _plan(*MAY_SKIP_E2E, "web/src/App.tsx").jobs


def test_editing_an_e2e_spec_always_runs_e2e() -> None:
    specs = sorted((REPO / "web" / "e2e").glob("*.spec.ts"))
    assert specs, "no e2e specs found: this sweep would pass vacuously"
    for spec in specs:
        rel = spec.relative_to(REPO).as_posix()
        assert "e2e" in _plan(rel).jobs, rel


def test_e2e_never_excludes_the_backend() -> None:
    e2e = _CAT.job("e2e")
    for path in ("src/personalclaw/app.py", "src/personalclaw/dashboard/sse.py"):
        assert planner.reaches(e2e.inputs, path), path


def _classifier_reasons() -> list[tuple[str, str]]:
    tree = ast.parse((REPO / "scripts" / "ci_touches_frontend.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "IGNORABLE":
            return list(ast.literal_eval(node.value))
    raise AssertionError("scripts/ci_touches_frontend.py has no IGNORABLE")


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_each_e2e_exclusion_carries_the_classifiers_reason_word_for_word() -> None:
    source = (REPO / catalogue.CATALOGUE).read_text(encoding="utf-8")
    block = source[: source.index('[[job]]\nid = "e2e"')]
    comment = _flat(" ".join(line.lstrip("# ") for line in block.splitlines()[-40:]))
    reasons = _classifier_reasons()
    assert len(reasons) == 6
    for what, why in reasons:
        assert _flat(f"{what}: {why}") in comment, what
        assert len(why) >= 60, f"{what}: a reason must say what makes the path unreachable"


# ── every declared input schedules its job ───────────────────────────────────────────────────

#: A path each job's declared inputs reach, and a path they do not (None: the whole tree).
_INPUT_ROWS = {
    "static": ("Makefile", None),
    "rails": ("tests/rails/test_ci_plan_tree.py", None),
    "python-suite": ("src/personalclaw/app.py", None),
    "python-suite-macos": ("src/personalclaw/app.py", None),
    "client": ("packages/personalclaw-client-py/src/x.py", None),
    "web-unit": ("web/src/App.tsx", None),
    "web-build": ("web/src/App.tsx", None),
    "package": ("setup.cfg", None),
    "e2e": ("web/src/App.tsx", "docs/vision.md"),
    "browse-live": ("src/personalclaw/browse/x.py", None),
    "apps-contract": ("src/personalclaw/sdk/channel.py", "docs/vision.md"),
    "audit": ("uv.lock", None),
}


def test_the_rows_name_every_job_of_the_change_and_merge_tiers() -> None:
    planned = {j.id for t in ("change", "merge") for j in _CAT.tier(t)}
    assert set(_INPUT_ROWS) == planned


@pytest.mark.parametrize("job_id", sorted(_INPUT_ROWS))
def test_a_declared_input_schedules_its_job(job_id: str) -> None:
    reached, missed = _INPUT_ROWS[job_id]
    tier = "merge" if job_id in _MERGE else "change"
    assert job_id in _plan(reached, tier=tier).jobs, reached
    if missed is not None:
        p = _plan(missed, tier=tier)
        assert not p.full and job_id not in p.jobs, missed


# ── the overrides, each with a planted path ──────────────────────────────────────────────────

#: Each override's planted path, and what it must plan: the jobs it adds, or the whole tier.
_OVERRIDE_ROWS = {
    ".github/workflows/ci.yml": "whole",
    ".github/workflows/release.yml": "whole",
    ".github/actions/setup/action.yml": "whole",
    "tools/ci/plan.py": "whole",
    "tools/ci/catalogue.toml": "whole",
    "tools/ci/areas.toml": "whole",
    "pyproject.toml": "whole",
    "uv.lock": "whole",
    "package.json": "whole",
    "package-lock.json": "whole",
    "deploy/website/install.sh": {"static"},
    "deploy/website/install.sh.sha256": {"static"},
    "deploy/docker/Dockerfile.backend": "whole",
    ".dockerignore": "whole",
    "setup.py": "whole",
    "MANIFEST.in": "whole",
    "tools/docker_single_container_smoke.py": "whole",
    "scripts/installed_bundled_model_probe.py": "whole",
    "scripts/sdk_signature_snapshot.py": {"apps-contract"},
}


@pytest.mark.parametrize("path", sorted(_OVERRIDE_ROWS))
def test_an_overrides_path_plans_what_it_says(path: str) -> None:
    """The override's own reason is in the plan: the whole tier because of it, or its job
    because of it, so neither the unknown-path fallback nor a whole-tree input stands in."""
    p, want = _plan(path), _OVERRIDE_ROWS[path]
    mine = [o for o in _AREAS if planner.reaches(o.paths, path)]
    assert mine, f"no override reaches {path}"
    if want == "whole":
        assert p.full and set(p.jobs) == _MERGE, path
        for override in (o for o in mine if o.whole_tier):
            assert any(override.reason in r for r in p.full_reasons), (path, p.full_reasons)
        assert any(o.whole_tier for o in mine), path
    else:
        assert set(want) <= set(p.jobs), (path, p.full_reasons)
        for job_id in want:
            assert any(o.reason in r for o in mine for r in p.reasons[job_id]), (path, job_id)


def test_a_local_action_a_job_calls_plans_the_whole_tier() -> None:
    """A local action's steps run inside each ci.yml job that calls it, and inside each action
    that calls it, so it defines those jobs as the workflow does: a change to any of its files
    plans the whole tier, as a change to ci.yml does."""
    jobs = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]
    actions = local_actions.actions_at(REPO)
    folders = sorted(
        {f for job in jobs.values() for f in local_actions.called(job.get("steps") or [], actions)}
    )
    assert folders, "ci.yml calls no local action, so this check read nothing"
    for folder in folders:
        for name in (*local_actions.ACTION_FILES, "run.sh"):
            rel = posixpath.normpath(f"{folder}/{name}")
            p = _plan(rel)
            assert p.full and set(p.jobs) == _MERGE, (rel, p.full_reasons)


def test_every_overrides_paths_have_a_planted_row() -> None:
    for override in _AREAS:
        rows = [p for p in _OVERRIDE_ROWS if planner.reaches(override.paths, p)]
        assert rows, f"no planted path for the override: {override.reason}"


def test_every_override_carries_a_reason_and_names_only_catalogue_jobs() -> None:
    ids = {j.id for j in _CAT.jobs}
    assert _AREAS
    for override in _AREAS:
        assert len(override.reason) >= 40, override
        assert set(override.jobs) <= ids, override


#: Every path a row above plants, and each kind of path the tree has besides.
_EVERY_ROW = sorted(
    {
        *_OVERRIDE_ROWS,
        *MUST_RUN_E2E,
        *MAY_SKIP_E2E,
        *(reached for reached, _ in _INPUT_ROWS.values()),
        "scripts/verify_wheel.py",
        "scripts/apps_sdk_contract.py",
        "deploy/docker/personalclaw-profile.sh",
        "src/personalclaw/sdk/channel.py",
    }
)


@pytest.mark.parametrize("path", _EVERY_ROW)
def test_the_overrides_never_remove_a_job(path: str) -> None:
    """An override only adds: the plan with every override holds every job the plan without
    any holds, the whole tier a path no input names included."""
    bare = planner.plan(_CAT, "merge", [path], overrides=(), root=REPO)
    p = _plan(path)
    assert set(bare.jobs) <= set(p.jobs), (path, set(bare.jobs) - set(p.jobs))
    declared = {j.id for j in _CAT.tier("merge") if planner.reaches(j.inputs, path)}
    assert declared <= set(p.jobs), (path, declared - set(p.jobs))


def test_no_job_builds_or_inspects_an_sdist_so_the_readme_needs_no_override() -> None:
    """The package job builds the wheel alone and verify_wheel.py inspects only it: no CI job
    reads the sdist, which is what requires README.md. A job that came to would need README.md
    among its inputs, or an override naming it."""
    for job in _CAT.jobs:
        for step in job.command:
            argv = list(step.argv)
            if argv[:2] == ["uv", "build"]:
                rest = argv[2:]
                values = {rest[i + 1] for i, a in enumerate(rest[:-1]) if a in ("--out-dir", "-o")}
                if not [a for a in rest if not a.startswith("-") and a not in values]:
                    assert "--wheel" in rest, f"{job.id} builds the core sdist: {argv}"
            if any(a.endswith("verify_wheel.py") for a in argv):
                assert "--wheel" in argv and "--build" not in argv, (job.id, argv)


def test_a_script_two_jobs_steps_share_is_an_override_not_a_classifier() -> None:
    """scripts/apps_sdk_contract.py imports the snapshot module: the planner reads that import
    from the step's script, and the override names it too, so a reader sees it in the catalogue."""
    p = _plan("scripts/sdk_signature_snapshot.py")
    assert not p.full and "apps-contract" in p.jobs
    assert any("imports it to compare" in r for r in p.reasons["apps-contract"])


# ── fail-safe, on the real catalogue ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "paths",
    [
        [],
        ["brand-new-top-level/x.ts"],
        [""],
        ["../outside"],
        ["docs/a\nb.md"],
        ["docs/vision.md\r"],
        ["docs/vision.md\0src/personalclaw/app.py"],
        ["./docs/vision.md"],
        ["docs"],
    ],
    ids=["empty", "unknown", "blank", "parent", "newline", "cr", "nul", "dot-slash", "folder-name"],
)
def test_what_the_planner_cannot_read_plans_the_whole_merge_tier(paths) -> None:
    p = planner.plan(_CAT, "merge", paths, overrides=_AREAS, root=REPO)
    assert p.full and set(p.jobs) == _MERGE


def test_no_plan_leaves_out_the_rails_or_a_security_or_live_job() -> None:
    floor = {
        j.id
        for j in _CAT.tier("merge")
        if j.id == catalogue.MERGE_NEEDS_JOB or j.security == "security" or j.live
    }
    assert {"rails", "python-suite", "python-suite-macos", "browse-live", "audit"} <= floor
    for path in [*MAY_SKIP_E2E, *MUST_RUN_E2E, *_OVERRIDE_ROWS]:
        assert floor <= set(_plan(path).jobs), path


def test_a_path_only_a_whole_tree_input_reaches_plans_the_whole_tier() -> None:
    """Every job but e2e and apps-contract declares the whole tree, which names no path: a
    backend or web change is therefore unknown and runs the whole tier, apps-contract included,
    until a job declares it. A narrower input is each job's own change."""
    for path in ("src/personalclaw/app.py", "web/src/App.tsx", "Makefile"):
        p = _plan(path)
        assert p.full and set(p.jobs) == _MERGE, path
        assert p.full_reasons == (f"{path} is named by no input but the whole tree",), path


def test_a_path_a_whole_tree_jobs_step_runs_is_still_unknown() -> None:
    """package's step runs scripts/verify_wheel.py: the step schedules package, but only a
    declared input names a path, and package declares the whole tree."""
    p = _plan("scripts/verify_wheel.py")
    assert p.full and set(p.jobs) == _MERGE
    assert p.full_reasons == ("scripts/verify_wheel.py is named by no input but the whole tree",)


def test_a_docs_only_plan_leaves_out_the_browser_run_and_reports_the_floor() -> None:
    """The issue's docs-only target (static and the rails alone) waits on each other job's
    inputs being narrowed; until then the plan keeps e2e off and reports the rails floor."""
    p = _plan("docs/vision.md", "README.md")
    assert not p.full and "e2e" not in p.jobs and "apps-contract" not in p.jobs
    assert {"static", "rails"} <= set(p.jobs)
    assert p.rails_floor == planner.NOT_MEASURED


def test_the_harness_replay_reason_is_kept_beside_the_overrides() -> None:
    source = _flat((REPO / catalogue.AREAS).read_text(encoding="utf-8").replace("#", " "))
    for path in (
        "web/src/pages/chat/",
        "web/src/pages/loops/",
        "src/personalclaw/dashboard/sse.py",
    ):
        assert path in source, path
    assert "stream-coalescer" in source and "run-fold" in source


def test_the_planner_imports_only_the_standard_library() -> None:
    import sys

    tree = ast.parse(textwrap.dedent((REPO / "tools/ci/plan.py").read_text(encoding="utf-8")))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
    }
    imported.discard("__future__")
    assert imported and imported <= set(sys.stdlib_module_names)
