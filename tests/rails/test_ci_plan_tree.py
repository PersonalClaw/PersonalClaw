"""The real catalogue's plans (tools/ci/plan.py over tools/ci/catalogue.toml).

It reads the tree's catalogue, its overrides (tools/ci/areas.toml), workflows and scripts, so it is
a rail: it runs once per CI run, in the rails job. tests/test_ci_plan.py holds the planner itself
on a planted catalogue.
"""

from __future__ import annotations

import ast
import posixpath
import subprocess
import sys
import textwrap
from functools import lru_cache

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


@lru_cache(maxsize=1)
def _tracked() -> tuple[str, ...]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return tuple(p for p in out.split("\0") if p)


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
    ".github/workflows/merge.yml",
    ".github/workflows/pr.yml",
    ".github/workflows/release.yml",
    ".github/workflows/breadth.yml",
    # The local action the jobs set their toolchain up with, which defines them as a workflow does.
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


#: The six exclusions of e2e's inputs, each with the reason the deleted ignore-list
#: (scripts/ci_touches_frontend.py) gave, word for word. The catalogue's comment must carry each.
_E2E_REASONS: tuple[tuple[str, str], ...] = (
    (
        "docs/**",
        (
            "prose only. Nothing under docs/ is served by the gateway or packaged into the "
            "wheel at runtime — pyproject.toml references it only as a Documentation URL."
        ),
    ),
    (
        "*.md at the repo root",
        (
            "README / CHANGELOG / CONTRIBUTING / AGENTS / SECURITY. Deliberately NOT '*.md "
            "anywhere': src/personalclaw/config/prompt_snippets/*.md is model input, not "
            "prose."
        ),
    ),
    (
        "tests/**",
        (
            "the pytest suite does not run inside the Playwright harness — the harness boots "
            "the gateway, not pytest — so a test-only change cannot alter scanned DOM."
        ),
    ),
    (
        "harness/**",
        (
            "repo-inner self-verification infra. src/personalclaw/ imports no `harness` "
            "module (grepped), so it is absent from the gateway the harness boots."
        ),
    ),
    (
        "packages/personalclaw-client-py/**",
        (
            "independently versioned/published client, NOT part of the core uv workspace "
            "(the `client` job builds it in its own venv) and not imported by "
            "src/personalclaw/."
        ),
    ),
    (
        ".github/** except workflows/ and actions/**",
        (
            "issue templates and dependabot config cannot change a rendered pixel. The "
            "workflows and the local actions under .github/actions/ are EXCLUDED from this "
            "exemption because they define the `e2e` job's run: merge.yml's stage job runs its "
            "steps, and a local action runs its own steps inside the job wherever the job "
            "calls it. Editing either must re-validate the job, or a broken job definition "
            "would be waved through by the very gate it broke."
        ),
    ),
)


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_each_e2e_exclusion_carries_its_reason_word_for_word() -> None:
    source = (REPO / catalogue.CATALOGUE).read_text(encoding="utf-8")
    block = source[: source.index('[[job]]\nid = "e2e"')]
    comment = _flat(" ".join(line.lstrip("# ") for line in block.splitlines()[-40:]))
    reasons = list(_E2E_REASONS)
    assert len(reasons) == 6
    for what, why in reasons:
        assert _flat(f"{what}: {why}") in comment, what
        assert len(why) >= 60, f"{what}: a reason must say what makes the path unreachable"


# ── every declared input schedules its job ───────────────────────────────────────────────────

#: A path each job's declared inputs reach, and a path they do not, whose plan leaves the job out
#: (None: no plan leaves it out, because the job declares the whole tree or is a job every plan
#: holds: the rails, a security job or a live one). python-suite names its inputs, but every plan
#: holds it as a security job, so its row pins the input through the reason alone.
#:
#: The browser run against the pinned image is e2e-stateful or e2e-pinned: #3943's rename lands
#: in two merges (#4584 step 4), this row first, so it takes the one name the catalogue holds.
_PINNED_BROWSER = ("e2e-stateful", "e2e-pinned")


def _pinned(ids: set[str]) -> list[str]:
    """The pinned browser run's name among *ids*: a list, so a test can see none or two."""
    return [n for n in _PINNED_BROWSER if n in ids]


_PINNED = _pinned({j.id for j in _CAT.jobs})
_INPUT_ROWS = {
    "static": ("Makefile", None),
    "rails": ("tests/rails/test_ci_plan_tree.py", None),
    "rails-checks": ("ASSET_LICENSES.json", None),
    "python-suite": ("src/personalclaw/app.py", None),
    "python-suite-macos": ("src/personalclaw/app.py", None),
    "client": ("packages/personalclaw-client-py/src/x.py", "docs/vision.md"),
    # The web, desktop and mobile tests plan for their own workspace: a desktop change plans no
    # web suite, and a web change no mobile tests.
    "web-unit": ("web/src/App.tsx", "desktop/main.js"),
    "desktop-unit": ("desktop/main.js", "src/personalclaw/app.py"),
    "mobile-unit": ("mobile/test/connect.test.mjs", "web/src/App.tsx"),
    "web-build": ("web/src/App.tsx", "docs/vision.md"),
    "desktop-smoke": ("desktop/main.js", "src/personalclaw/app.py"),
    "package": ("setup.cfg", "docs/vision.md"),
    # A backend-only change boots the image it ships (#3917).
    "image": ("src/personalclaw/app.py", "docs/vision.md"),
    "e2e": ("web/src/App.tsx", "docs/vision.md"),
    "e2e-shard": ("web/src/App.tsx", "docs/vision.md"),
    **{name: ("web/src/App.tsx", "docs/vision.md") for name in _PINNED},
    "ui-quality": ("web/src/App.tsx", "docs/vision.md"),
    "browse-live": ("src/personalclaw/browse/x.py", None),
    "security": ("tests/security/test_x.py", None),
    "apps-contract": ("src/personalclaw/sdk/channel.py", "docs/vision.md"),
    "audit": ("uv.lock", "docs/vision.md"),
    "namespace-sandbox": ("src/personalclaw/sandbox.py", None),
}


def test_the_rows_name_every_job_of_the_change_and_merge_tiers() -> None:
    planned = {j.id for t in ("change", "merge") for j in _CAT.tier(t)}
    assert set(_INPUT_ROWS) == planned


def test_the_catalogue_names_the_pinned_browser_run_once() -> None:
    assert len(_PINNED) == 1, f"one of {_PINNED_BROWSER}, once: {_PINNED}"


@pytest.mark.parametrize(
    "ids, count",
    [
        ({"e2e", "e2e-stateful"}, 1),
        ({"e2e", "e2e-pinned"}, 1),
        ({"e2e", "e2e-other"}, 0),
        ({"e2e-stateful", "e2e-pinned"}, 2),
    ],
    ids=["the old name", "the new name", "a third name", "both names"],
)
def test_the_pinned_browser_run_is_read_under_either_name_and_only_one(
    ids: set[str], count: int
) -> None:
    assert len(_pinned(ids)) == count


@pytest.mark.parametrize("job_id", sorted(_INPUT_ROWS))
def test_a_declared_input_schedules_its_job(job_id: str) -> None:
    reached, missed = _INPUT_ROWS[job_id]
    tier = "merge" if job_id in _MERGE else "change"
    p = _plan(reached, tier=tier)
    assert job_id in p.jobs, reached
    assert f"{reached}: a declared input" in p.reasons[job_id], (reached, p.reasons[job_id])
    if missed is not None:
        p = _plan(missed, tier=tier)
        assert not p.full and job_id not in p.jobs, missed


# ── the overrides, each with a planted path ──────────────────────────────────────────────────

#: Each override's planted path, and what it must plan: the jobs it adds, or the whole tier.
_OVERRIDE_ROWS = {
    ".github/workflows/merge.yml": "whole",
    ".github/workflows/pr.yml": "whole",
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
    # What the harness's own rule once forced replay on (#3945): the gates that hold replay.
    "web/src/pages/chat/coalesceReducers.ts": {"web-unit", "python-suite"},
    "web/src/pages/loops/LoopRun.tsx": {"web-unit", "python-suite"},
    "src/personalclaw/dashboard/sse.py": {"web-unit", "python-suite"},
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


#: The folders a whole-tier override names, written here and not read from tools/ci/areas.toml: an
#: override narrowed to the files planted above would still pass the rows above, and every other
#: file under its folder would lose the whole tier its reason promises. python-suite names each
#: of these folders, so only the override plans their files' whole tier.
_WHOLE_TIER_FOLDERS = (".github/workflows/", ".github/actions/", "tools/ci/", "deploy/docker/")


@pytest.mark.parametrize("folder", _WHOLE_TIER_FOLDERS)
def test_every_tracked_file_under_a_whole_tier_folder_plans_the_whole_tier(folder: str) -> None:
    under = [p for p in _tracked() if p.startswith(folder)]
    assert under, f"no tracked file under {folder}: this sweep would read nothing"
    reasons = [o.reason for o in _AREAS if o.whole_tier]
    for path in under:
        p = _plan(path)
        assert p.full and set(p.jobs) == _MERGE, (path, p.full_reasons)
        assert any(why in r for why in reasons for r in p.full_reasons), (path, p.full_reasons)


@pytest.mark.parametrize("workflow", ("pr.yml", "merge.yml"))
def test_a_local_action_a_job_calls_plans_the_whole_tier(workflow: str) -> None:
    """A local action's steps run inside each tier job that calls it, and inside each action
    that calls it, so it defines those jobs as the workflow does: a change to any of its files
    plans the whole tier, as a change to the workflow does."""
    path = REPO / ".github" / "workflows" / workflow
    jobs = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
    actions = local_actions.actions_at(REPO)
    folders = sorted(
        {f for job in jobs.values() for f in local_actions.called(job.get("steps") or [], actions)}
    )
    assert folders, f"{workflow} calls no local action, so this check read nothing"
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
    """The rails and their own check (which holds every rail to one half) are in every plan,
    with the security jobs and the live ones breadth does not run every night."""
    always = (catalogue.MERGE_NEEDS_JOB, catalogue.RAILS_CHECKS_JOB)
    floor = {
        j.id
        for j in _CAT.tier("merge")
        if j.id in always or j.security == "security" or (j.live and j.nightly is None)
    }
    assert {"rails", "rails-checks", "python-suite", "python-suite-macos", "browse-live"} <= floor
    assert "audit" not in floor, "audit's nightly mark lets the plan select it by its inputs"
    for path in [*MAY_SKIP_E2E, *MUST_RUN_E2E, *_OVERRIDE_ROWS]:
        assert floor <= set(_plan(path).jobs), path


def test_a_src_diff_is_known_and_leaves_apps_contract_out() -> None:
    """python-suite names src/, so a backend diff is a known path: the plan is not the whole
    tier, and apps-contract, whose inputs are the SDK's, is left out."""
    p = _plan("src/personalclaw/app.py")
    assert not p.full, p.full_reasons
    assert {"python-suite", "python-suite-macos", "e2e"} <= set(p.jobs)
    assert "apps-contract" not in p.jobs
    assert any("a declared input" in r for r in p.reasons["python-suite"])


def test_a_src_diff_that_reaches_the_sdk_plans_apps_contract() -> None:
    p = _plan("src/personalclaw/app.py", "src/personalclaw/sdk/channel.py")
    assert not p.full and "apps-contract" in p.jobs


def test_the_desktop_smoke_runs_for_the_shell_and_the_dashboard_and_for_neither_else() -> None:
    """The packed shell's smoke (#3942) is planned when the shell or the dashboard it loads
    changed, and left out of a diff that touches neither: a backend change is e2e's to catch, as
    the smoke proves the shell starts and draws, not the gateway."""
    for path in ("desktop/main.js", "desktop/package.json", "web/src/App.tsx", "web/index.html"):
        p = _plan(path)
        assert "desktop-smoke" in p.jobs, path
    for path in (
        "src/personalclaw/app.py",
        "docs/vision.md",
        "packages/personalclaw-client-py/src/x.py",
    ):
        p = _plan(path)
        assert not p.full and "desktop-smoke" not in p.jobs, path


def test_docs_is_one_of_python_suites_inputs() -> None:
    """Suite modules read docs/ (the publication-hygiene check reads every tracked file, and
    modules check prose against the tree), so docs/ is one of python-suite's inputs. Every plan
    holds python-suite as a security job anyway: this row pins the input, through its reason,
    not the run."""
    p = _plan("docs/vision.md")
    assert not p.full and "python-suite" in p.jobs and "python-suite-macos" in p.jobs
    assert "docs/vision.md: a declared input" in p.reasons["python-suite"], p.reasons


def test_a_path_only_a_whole_tree_input_reaches_plans_the_whole_tier() -> None:
    """flake.nix is in no job's named inputs: the suite reads it (the publication-hygiene check
    opens every tracked file), and python-suite leaves it out on purpose, so a change to it alone
    is unknown and plans the whole tier
    (tests/rails/test_ci_python_suite_inputs_tree.py holds that choice)."""
    p = _plan("flake.nix")
    assert p.full and set(p.jobs) == _MERGE
    assert p.full_reasons == ("flake.nix is named by no input but the whole tree",)


def test_a_new_root_markdown_file_is_known_through_e2es_exclusion() -> None:
    """A new top-level path no input names plans the whole tier, but a new root *.md is named by
    e2e's exclusion: it is known, and its plan leaves e2e and apps-contract out."""
    assert "GOVERNANCE.md" not in _tracked()
    p = _plan("GOVERNANCE.md")
    assert not p.full, p.full_reasons
    assert "e2e" not in p.jobs and "apps-contract" not in p.jobs


#: The triggers of the SDK classifier ci.yml ran apps-contract by before the plan
#: (scripts/ci_touches_sdk.py, deleted), word for word, less that classifier's own file: a path
#: that starts with a folder here, or is a file here, ran the job.
_SDK_TRIGGERS: tuple[str, ...] = (
    "src/personalclaw/sdk/",
    "scripts/apps_sdk_contract.py",
    "scripts/sdk_signature_snapshot.py",
    ".github/workflows/ci.yml",
    ".github/actions/",
)

#: The trigger #3915 deleted with ci.yml. The workflows that replaced it plan the whole tier,
#: apps-contract with it (tools/ci/areas.toml), so no input of the job names a workflow.
_DELETED_TRIGGER = ".github/workflows/ci.yml"


def _the_classifier_ran_apps_contract(path: str) -> bool:
    return any(path.startswith(t) if t.endswith("/") else path == t for t in _SDK_TRIGGERS)


def test_apps_contracts_inputs_are_the_sdk_classifiers_triggers() -> None:
    """ci.yml ran apps-contract on what the SDK classifier said, and the catalogue's inputs are
    that classifier's triggers, less the deleted workflow: the two lists are one."""
    assert set(_CAT.job("apps-contract").inputs) == set(_SDK_TRIGGERS) - {_DELETED_TRIGGER}


def test_every_tracked_path_the_classifier_ran_apps_contract_on_plans_it() -> None:
    """python-suite names every top-level entry but the flake, so the unknown-path fallback no
    longer plans apps-contract for a path its inputs miss: each tracked path the classifier
    ran it on must plan it."""
    sdk = [p for p in _tracked() if _the_classifier_ran_apps_contract(p)]
    assert len(sdk) > 20, sdk
    missed = [p for p in sdk if "apps-contract" not in _plan(p).jobs]
    assert not missed, missed


def test_a_docs_only_plan_leaves_out_the_browser_run_and_reports_the_floor() -> None:
    """The issue's docs-only target (static and the rails alone) waits on two things: each other
    job's inputs being narrowed, and a security-posture decision, since every plan holds the
    security jobs (python-suite among them) whatever their inputs say. Until then the plan keeps
    e2e off and reports the rails floor."""
    p = _plan("docs/vision.md", "README.md")
    assert not p.full and "e2e" not in p.jobs and "apps-contract" not in p.jobs
    assert {"static", "rails"} <= set(p.jobs)
    assert p.rails_floor == planner.NOT_MEASURED


def test_the_harness_replay_reason_is_an_override_the_planner_reads() -> None:
    """The rule the harness once kept beside the planner (``harness/selection.py``) is one of
    its overrides now, word for word in its reason, so ``harness run --diff`` and CI plan replay's
    paths alike."""
    replay = [o for o in _AREAS if "stream-coalescer" in o.reason and "run-fold" in o.reason]
    assert len(replay) == 1, [o.reason for o in _AREAS]
    for path in (
        "web/src/pages/chat/",
        "web/src/pages/loops/",
        "src/personalclaw/dashboard/sse.py",
    ):
        assert path in replay[0].paths, path
    assert {"web-unit", "python-suite"} <= set(replay[0].jobs)


def test_the_planner_imports_only_the_standard_library() -> None:
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
