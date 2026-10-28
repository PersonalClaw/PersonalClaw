"""Every setup-uv step that keeps uv's cache between runs prunes it before saving.

setup-uv saves uv's cache (``UV_CACHE_DIR``) at the end of a job and restores it at the start of
the next one. Pruned for CI (``uv cache prune --ci``, which setup-uv runs when ``prune-cache`` is
true), the cache keeps only the wheels uv had to build from source. Unpruned, it keeps every wheel
the job downloaded, and every later run restores all of them before anything else: a macOS test
job spent 14-20 s restoring a 261 MB cache so that ``uv sync`` could skip downloads it otherwise
makes in about 8 s.

setup-uv's own default changed under us: ``prune-cache`` defaulted to true up to v7 and defaults
to false from v10. So a version bump alone turned pruning off and changed the cache key, and the
next run saved the full cache. The rule does not lean on the default: every setup-uv step either
turns caching off (``enable-cache: false``) or sets ``prune-cache: true`` itself. ``enable-cache``
left out means ``auto``, which caches on GitHub's runners for every event but a release, a tag
push, ``pull_request_target`` and ``workflow_run``, so it is held to the rule too.

The planted workflows below show the rule fails on a caching step with no ``prune-cache``, with
``prune-cache: false`` as a boolean and as a string, and with ``enable-cache`` left out or
``auto``, and passes on a pruned step and on one that caches nothing.
"""

from __future__ import annotations

import copy
import fnmatch

import local_actions
import pytest
import yaml
from rails_repo import REPO

ROOT = REPO
WORKFLOWS = ROOT / ".github" / "workflows"


def _flag(value: object) -> str:
    """A ``with:`` input as setup-uv reads it: YAML's true is the string "true"."""
    return str(value).strip().lower()


def setup_uv_steps(workflow: dict, actions: dict | None = None) -> list[tuple[str, dict]]:
    """``(job id, step)`` for every setup-uv step in one parsed workflow, a step inside a local
    composite action the job calls included (*actions*, as ``local_actions.action_files``)."""
    found = []
    for job_id, job in (workflow.get("jobs") or {}).items():
        for _index, step in local_actions.expand((job or {}).get("steps") or [], actions or {}):
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
                found.append((job_id, step))
    return found


def _actions() -> dict:
    return local_actions.actions_at(ROOT)


def unpruned_caches(name: str, workflow: dict, actions: dict | None = None) -> list[str]:
    """One problem line for each setup-uv step that would save uv's cache unpruned."""
    problems = []
    for job_id, step in setup_uv_steps(workflow, actions):
        given = step.get("with") or {}
        enabled = _flag(given.get("enable-cache", "auto"))
        if enabled == "false":
            continue
        if _flag(given.get("prune-cache", "")) != "true":
            where = f"{name} {job_id} {step.get('name') or step['uses'].split('@')[0]!r}"
            problems.append(
                f"{where}: keeps uv's cache (enable-cache: {enabled}) without "
                "prune-cache: true, so it saves every downloaded wheel and every "
                "run restores them"
            )
    return problems


def _workflows() -> dict[str, dict]:
    paths = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])
    return {p.name: yaml.safe_load(p.read_text(encoding="utf-8")) for p in paths}


def test_the_rule_reads_every_setup_uv_step_the_workflows_have():
    """Vacuity floor: breadth.yml installs uv in most of its jobs, each stage job of the tier
    workflows does through the setup action (merge.yml's four, pr.yml's one), and the release,
    Nix and headless-smoke workflows each install it too."""
    actions = _actions()
    found = {name: len(setup_uv_steps(wf, actions)) for name, wf in _workflows().items()}
    assert found.get("breadth.yml", 0) >= 5, found
    assert found.get("merge.yml", 0) >= 4 and found.get("pr.yml", 0) >= 1, found
    assert {"release-artifacts.yml", "nix.yml", "headless-run-smoke.yml"} <= {
        n for n, c in found.items() if c
    }


def test_every_setup_uv_step_that_caches_prunes_first():
    actions = _actions()
    problems = [
        line for name, wf in _workflows().items() for line in unpruned_caches(name, wf, actions)
    ]
    assert not problems, "\n".join(problems)


def test_a_setup_uv_step_inside_the_action_a_job_calls_is_held():
    """A job that sets up uv through a local action is held to the action's step."""
    action = {
        "inputs": {"python-version": {"default": ""}},
        "runs": {
            "using": "composite",
            "steps": [{"name": "Install uv", "uses": SETUP_UV, "with": {"enable-cache": True}}],
        },
    }
    call = {"uses": "./.github/actions/setup", "with": {"python-version": "3.12.15"}}
    workflow = {"jobs": {"build": {"steps": [call]}}}
    problems = unpruned_caches("planted.yml", workflow, {"./.github/actions/setup": action})
    assert len(problems) == 1 and "enable-cache: true" in problems[0], problems


# -- a call read as the runner runs it ---------------------------------------------------------
#
# The reader (tests/rails/local_actions.py) drops an action step only when its `if:` is false
# for the call as GitHub evaluates it. Each case below is a call GitHub runs the unpruned step
# for, so the rule must still see it; the last is one GitHub skips.

_ACTION_NAME = "./.github/actions/setup"


def _gated(
    condition: str,
    value: object,
    name: str = "uv",
    declared: tuple[str, object] = ("uv", "false"),
) -> list[str]:
    """The problems for one job calling an action whose unpruned setup-uv step runs when
    *condition* holds, with the call's input *name* set to *value* (``None``: not given), and
    the action declaring the input ``declared[0]`` with the default ``declared[1]``."""
    step = {"name": "Install uv", "if": condition, "uses": SETUP_UV, "with": {"enable-cache": True}}
    action = {
        "inputs": {declared[0]: {"default": declared[1]}},
        "runs": {"using": "composite", "steps": [step]},
    }
    call = {"uses": _ACTION_NAME, "with": {} if value is None else {name: value}}
    workflow = {"jobs": {"build": {"steps": [call]}}}
    return unpruned_caches("planted.yml", workflow, {_ACTION_NAME: action})


@pytest.mark.parametrize(
    ("condition", "value"),
    [
        ("inputs.uv == 'true'", "true"),
        ("${{ inputs.uv == 'true' }}", True),
        ("inputs.uv == 'true'", "TRUE"),
        ("inputs.UV == 'true'", "true"),
        ("inputs.uv == 'true'", "${{ matrix.uv }}"),
        ("inputs.uv == 'true'", "t${{ matrix.uv }}"),
        ("inputs.uv != 'k'", "\u212a"),
        ("inputs.uv == 'true' && github.event_name == 'push'", "false"),
        ("always()", "false"),
        ("${{ inputs.uv == 'true' }}\n", "false"),
        (" ${{ inputs.uv == 'true' }}", "false"),
        ("inputs.uv == '${{ github.job }}'", "false"),
    ],
    ids=[
        "the input set",
        "a wrapped condition and a YAML true",
        "the input in capitals: GitHub ignores case",
        "the input named in capitals: so do its names",
        "the input an expression only the runner evaluates",
        "an expression inside the input",
        "a value outside ASCII, which the reader does not case-fold",
        "a condition the reader cannot evaluate",
        "a status function",
        # Text outside one ${{ }} makes the condition a string GitHub fills in, never empty.
        "a block scalar's newline after the expression",
        "a space before the expression",
        "an expression in the literal",
    ],
)
def test_a_step_github_runs_for_the_call_is_held(condition, value):
    problems = _gated(condition, value)
    assert len(problems) == 1 and "enable-cache: true" in problems[0], problems


@pytest.mark.parametrize(
    ("name", "declared", "value"),
    [("UV", ("uv", "false"), "true"), ("uv", ("UV", "true"), None)],
    ids=["the call names the input in capitals", "the action declares it in capitals"],
)
def test_an_input_name_ignores_case_on_both_sides_of_the_call(name, declared, value):
    problems = _gated("inputs.uv == 'true'", value, name, declared)
    assert len(problems) == 1 and "enable-cache: true" in problems[0], problems


def test_a_block_scalar_condition_written_in_yaml_is_held():
    """The block scalar as an action file writes it: YAML keeps its newline after ``}}``."""
    text = (
        "inputs:\n  uv:\n    default: 'false'\n"
        "runs:\n  using: composite\n  steps:\n"
        "    - name: Install uv\n      if: |\n        ${{ inputs.uv == 'true' }}\n"
        f"      uses: {SETUP_UV}\n      with:\n        enable-cache: true\n"
    )
    actions = local_actions.action_files({".github/actions/setup/action.yml": text})
    workflow = {"jobs": {"build": {"steps": [{"uses": _ACTION_NAME, "with": {"uv": "false"}}]}}}
    problems = unpruned_caches("planted.yml", workflow, actions)
    assert len(problems) == 1 and "enable-cache: true" in problems[0], problems


@pytest.mark.parametrize(
    ("condition", "word", "given"),
    [
        ("inputs.uv == 'yes'", "yes", True),
        ("inputs.uv != 'false'", "off", True),
        ("inputs.uv == 'on'", "on", False),
    ],
    ids=["a call's yes", "a call's off", "a default's on"],
)
def test_a_yaml_1_1_boolean_is_held(condition, word, given):
    """YAML 1.1 reads yes, no, on and off as booleans, where GitHub hands on the word itself. The
    reader cannot tell which word a boolean was written as, so a condition on it holds the step."""
    value = yaml.safe_load(word)
    assert isinstance(value, bool)
    if given:
        problems = _gated(condition, value)
    else:
        problems = _gated(condition, None, declared=("uv", value))
    assert len(problems) == 1 and "enable-cache: true" in problems[0], problems


def _run_by(call: dict, actions: dict) -> list[dict]:
    """The action steps one *call* runs, as the reader reads them."""
    return [step for _index, step in local_actions.expand([call], actions) if step is not call]


def test_a_call_fills_each_input_its_steps_name():
    """``${{ inputs.<name> }}`` reads the call's value, or the action's default, wherever a
    rail reads the step: its ``with:``, ``env:``, ``run:`` and ``working-directory:``."""
    steps = [
        {
            "uses": SETUP_UV,
            "with": {"version": "${{ inputs.uv }}"},
            "env": {"E": "${{ inputs.V }}"},
        },
        {"run": "npm ci # ${{ inputs.v }}", "working-directory": "${{ inputs.dir }}"},
    ]
    action = {
        "inputs": {"uv": {"default": "0.13.1"}, "v": {}, "dir": {"default": "web"}},
        "runs": {"using": "composite", "steps": steps},
    }
    call = {"uses": _ACTION_NAME, "with": {"v": "given"}}
    read = _run_by(call, {_ACTION_NAME: action})
    assert read[0]["with"] == {"version": "0.13.1"} and read[0]["env"] == {"E": "given"}, read
    assert read[1]["run"] == "npm ci # given" and read[1]["working-directory"] == "web", read


def _uv_action(**step: object) -> dict:
    """An action whose one step is setup-uv, with *step*'s keys beside its ``uses:``."""
    return {
        "runs": {"using": "composite", "steps": [{"name": "Install uv", "uses": SETUP_UV, **step}]}
    }


def test_a_steps_own_env_wins_over_the_calls():
    """The call's ``env:`` sits beneath each step's own: a name both set is the step's."""
    call = {"uses": _ACTION_NAME, "env": {"UV_PYTHON_PREFERENCE": "only-managed"}}
    read = _run_by(call, {_ACTION_NAME: _uv_action(env={"UV_PYTHON_PREFERENCE": "system"})})
    assert read[0]["env"] == {"UV_PYTHON_PREFERENCE": "system"}, read


def test_the_calls_env_reaches_a_step_with_an_env_of_its_own():
    call = {"uses": _ACTION_NAME, "env": {"UV_PYTHON_PREFERENCE": "system"}}
    read = _run_by(call, {_ACTION_NAME: _uv_action(env={"OTHER": "x"})})
    assert read[0]["env"] == {"UV_PYTHON_PREFERENCE": "system", "OTHER": "x"}, read


def test_a_nested_action_gets_the_outer_calls_env_and_filled_inputs():
    """An action another action calls runs with the inputs its call fills from the outer call,
    and with the outer call's ``env:``."""
    inner = {
        "inputs": {"py": {"default": ""}},
        "runs": {
            "using": "composite",
            "steps": [
                {
                    "name": "Install uv",
                    "uses": SETUP_UV,
                    "with": {"python-version": "${{ inputs.py }}"},
                }
            ],
        },
    }
    outer = {
        "inputs": {"py": {"default": ""}},
        "runs": {
            "using": "composite",
            "steps": [{"uses": "./.github/actions/inner", "with": {"py": "${{ inputs.py }}"}}],
        },
    }
    call = {
        "uses": _ACTION_NAME,
        "with": {"py": "3.12.15"},
        "env": {"UV_PYTHON_PREFERENCE": "system"},
    }
    read = _run_by(call, {_ACTION_NAME: outer, "./.github/actions/inner": inner})
    uv = [step for step in read if step.get("uses") == SETUP_UV]
    assert len(uv) == 1, read
    assert uv[0]["with"] == {"python-version": "3.12.15"}, uv
    assert uv[0]["env"] == {"UV_PYTHON_PREFERENCE": "system"}, uv


def test_a_step_github_skips_for_the_call_is_not_read():
    assert _gated("inputs.uv == 'true'", "false") == []
    assert _gated("inputs.uv != ''", "") == []
    assert _gated("${{ inputs.uv == 'true' }}", "false") == []


def test_a_setup_uv_step_in_an_action_the_action_calls_is_held():
    inner = {"runs": {"using": "composite", "steps": [{"name": "Install uv", "uses": SETUP_UV}]}}
    outer = {"runs": {"using": "composite", "steps": [{"uses": "./.github/actions/inner/"}]}}
    workflow = {"jobs": {"build": {"steps": [{"uses": _ACTION_NAME}]}}}
    actions = {_ACTION_NAME: outer, "./.github/actions/inner": inner}
    problems = unpruned_caches("planted.yml", workflow, actions)
    assert len(problems) == 1 and "enable-cache: auto" in problems[0], problems


@pytest.mark.parametrize(
    ("uses", "actions"),
    [
        (_ACTION_NAME, {}),
        (
            _ACTION_NAME,
            {_ACTION_NAME: {"runs": {"using": "composite", "steps": [{"uses": _ACTION_NAME}]}}},
        ),
        ("./tools/ci/setup", {_ACTION_NAME: {"runs": {"using": "composite", "steps": []}}}),
    ],
    ids=[
        "an action the reader was not given",
        "an action that calls itself",
        "a local action outside .github/actions",
    ],
)
def test_an_action_the_reader_cannot_read_through_fails(uses, actions):
    workflow = {"jobs": {"build": {"steps": [{"uses": uses}]}}}
    with pytest.raises(local_actions.UnreadAction):
        unpruned_caches("planted.yml", workflow, actions)


def test_an_action_yaml_is_read_and_an_action_yml_beside_it_wins():
    tree = {
        ".github/actions/a/action.yaml": "name: a\n",
        ".github/actions/b/action.yaml": "name: yaml\n",
        ".github/actions/b/action.yml": "name: yml\n",
        ".github/actions/c/d/action.yml": "name: d\n",
    }
    found = {name: data["name"] for name, data in local_actions.action_files(tree).items()}
    assert found == {
        "./.github/actions/a": "a",
        "./.github/actions/b": "yml",
        "./.github/actions/c/d": "d",
    }


def test_an_action_is_found_at_any_depth_under_github_actions(tmp_path):
    for rel in (
        ".github/actions/a/action.yml",
        ".github/actions/b/c/action.yaml",
        ".github/workflows/action.yml",
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("name: x\n", encoding="utf-8")
    found = [p.relative_to(tmp_path).as_posix() for p in local_actions.action_paths(tmp_path)]
    assert found == [".github/actions/a/action.yml", ".github/actions/b/c/action.yaml"], found


# -- planted workflows ------------------------------------------------------------------------

SETUP_UV = "astral-sh/setup-uv@1c37ad07a6a961277cf70c0d37d6f313000f5884"
PLANTED = {
    "jobs": {
        "build": {
            "steps": [
                {
                    "name": "Install uv",
                    "uses": SETUP_UV,
                    "with": {"version": "0.13.1", "enable-cache": True, "prune-cache": True},
                }
            ]
        }
    }
}


def _planted(**with_inputs: object) -> dict:
    workflow = copy.deepcopy(PLANTED)
    given = workflow["jobs"]["build"]["steps"][0]["with"]
    for key, value in with_inputs.items():
        if value is None:
            given.pop(key.replace("_", "-"), None)
        else:
            given[key.replace("_", "-")] = value
    return workflow


def test_the_planted_baseline_passes():
    assert unpruned_caches("planted.yml", _planted()) == []


@pytest.mark.parametrize(
    ("inputs", "shown"),
    [
        ({"prune_cache": None}, "enable-cache: true"),
        ({"prune_cache": False}, "enable-cache: true"),
        ({"prune_cache": "false"}, "enable-cache: true"),
        ({"enable_cache": None, "prune_cache": None}, "enable-cache: auto"),
        ({"enable_cache": "auto", "prune_cache": None}, "enable-cache: auto"),
    ],
    ids=[
        "no prune-cache",
        "prune-cache false",
        "prune-cache 'false'",
        "enable-cache left out",
        "enable-cache auto",
    ],
)
def test_a_planted_step_that_saves_the_cache_unpruned_fails(inputs, shown):
    problems = unpruned_caches("planted.yml", _planted(**inputs))
    assert len(problems) == 1 and problems[0].startswith("planted.yml build 'Install uv'"), problems
    assert shown in problems[0] and "prune-cache: true" in problems[0]


def test_a_planted_step_with_no_inputs_at_all_fails():
    """No ``with:`` block is ``enable-cache: auto`` with setup-uv's own ``prune-cache``."""
    workflow = {"jobs": {"build": {"steps": [{"uses": SETUP_UV}]}}}
    problems = unpruned_caches("planted.yml", workflow)
    assert len(problems) == 1 and "enable-cache: auto" in problems[0], problems


@pytest.mark.parametrize(
    "inputs",
    [
        {"enable_cache": False, "prune_cache": None},
        {"enable_cache": "false", "prune_cache": None},
        {"prune_cache": "true"},
    ],
    ids=["caching off", "caching off as a string", "prune-cache as a string"],
)
def test_a_planted_step_that_caches_nothing_or_prunes_passes(inputs):
    assert unpruned_caches("planted.yml", _planted(**inputs)) == []


def test_a_step_of_another_action_is_not_read():
    workflow = {
        "jobs": {"build": {"steps": [{"uses": "actions/setup-node@x", "with": {"cache": "npm"}}]}}
    }
    assert setup_uv_steps(workflow) == [] and unpruned_caches("planted.yml", workflow) == []


# -- managed Python in the cache ---------------------------------------------------------------
#
# ``cache-python: true`` saves the interpreter uv installed (``UV_PYTHON_INSTALL_DIR``) to the
# Actions cache with uv's own, and restores it in the next job. That puts a binary in the cache,
# so a step may cache it only where it keeps uv's cache by its own say (``enable-cache: true``:
# ``auto`` caches for whichever events setup-uv picks), and never on the release path: a release
# workflow, a workflow a release tag's push starts, or one either calls. The release path builds
# and publishes what users run, so it restores no interpreter a pull request's or main's run left
# behind, and no cache at all: each of its setup-uv steps turns caching off itself
# (``enable-cache: false``), since ``auto`` restores one on a release's dispatch and a
# rehearsal's schedule.


#: A release tag, as release.yml's own filter (``v*``) takes it. A rehearsal's tag
#: (``rehearsal-v*``, which starts breadth on the candidate) is not one: breadth tests, and builds
#: and publishes nothing.
_RELEASE_TAG = "v0.0.0"


def release_path(workflows: dict[str, dict]) -> set[str]:
    """The workflows a release runs: each named ``release*``, each whose tag filter a release tag's
    push matches, and each one of those calls (``jobs.<id>.uses: ./.github/workflows/<name>``),
    however deep."""
    found = set()
    for name, wf in workflows.items():
        on = wf.get("on", wf.get(True)) or {}
        push = on.get("push") if isinstance(on, dict) else None
        tags = push.get("tags") if isinstance(push, dict) else None
        released = any(fnmatch.fnmatchcase(_RELEASE_TAG, str(tag)) for tag in tags or [])
        if name.startswith("release") or released:
            found.add(name)
    queue = list(found)
    while queue:
        for job in (workflows[queue.pop()].get("jobs") or {}).values():
            called = str((job or {}).get("uses", ""))
            if called.startswith("./.github/workflows/"):
                name = called.removeprefix("./.github/workflows/").split("@")[0]
                if name in workflows and name not in found:
                    found.add(name)
                    queue.append(name)
    return found


def python_caches(
    name: str, workflow: dict, actions: dict | None = None, release: frozenset[str] = frozenset()
) -> list[str]:
    """One problem line for each setup-uv step that caches managed Python where it may not."""
    problems = []
    for job_id, step in setup_uv_steps(workflow, actions):
        given = step.get("with") or {}
        if _flag(given.get("cache-python", "false")) == "false":
            continue
        where = f"{name} {job_id} {step.get('name') or step['uses'].split('@')[0]!r}"
        enabled = _flag(given.get("enable-cache", "auto"))
        if enabled != "true":
            problems.append(
                f"{where}: caches managed Python with enable-cache: {enabled}: keep uv's cache by"
                " the step's own say (enable-cache: true) to cache the interpreter beside it"
            )
        if name in release:
            problems.append(
                f"{where}: caches managed Python on the release path, which restores no"
                " interpreter another run left in the cache"
            )
    return problems


def release_caches(name: str, workflow: dict, actions: dict | None = None) -> list[str]:
    """One problem line for each setup-uv step of a release-path workflow that keeps a cache."""
    problems = []
    for job_id, step in setup_uv_steps(workflow, actions):
        enabled = _flag((step.get("with") or {}).get("enable-cache", "auto"))
        if enabled != "false":
            where = f"{name} {job_id} {step.get('name') or step['uses'].split('@')[0]!r}"
            problems.append(
                f"{where}: keeps uv's cache on the release path (enable-cache: {enabled}), so a"
                " release restores what another run left: turn it off (enable-cache: false)"
            )
    return problems


def _python_problems(workflows: dict[str, dict], actions: dict) -> list[str]:
    release = frozenset(release_path(workflows))
    found = [line for n, wf in workflows.items() for line in python_caches(n, wf, actions, release)]
    return found + [
        line for n in sorted(release) for line in release_caches(n, workflows[n], actions)
    ]


def test_managed_python_is_cached_only_where_uv_s_cache_is_and_never_on_the_release_path():
    workflows, actions = _workflows(), _actions()
    assert _python_problems(workflows, actions) == []
    # Not vacuous: the setup action's step caches it, for the tier workflows' stage jobs.
    caching = {
        name
        for name, wf in workflows.items()
        for _job, step in setup_uv_steps(wf, actions)
        if _flag((step.get("with") or {}).get("cache-python", "false")) == "true"
    }
    assert {"merge.yml", "pr.yml"} <= caching, caching
    # And the path is the tree's: a release calls the artifacts build, and a tag push starts one.
    assert {"release.yml", "release-artifacts.yml", "release-rehearsal.yml"} <= release_path(
        workflows
    )
    assert "breadth.yml" not in release_path(workflows)
    assert not caching & release_path(workflows), caching & release_path(workflows)
    # And the release path's own setup-uv steps are read: the artifacts build has them.
    assert setup_uv_steps(workflows["release-artifacts.yml"], actions)


@pytest.mark.parametrize(
    "given",
    [{"enable_cache": True}, {"enable_cache": "auto"}, {"enable_cache": None}],
    ids=["enable-cache true", "enable-cache auto", "enable-cache left out"],
)
def test_a_release_build_that_keeps_uv_s_cache_fails(given):
    workflows, actions = _workflows(), _actions()
    planted = copy.deepcopy(workflows["release-artifacts.yml"])
    for job in (planted.get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
                inputs = step.setdefault("with", {})
                for key, value in given.items():
                    if value is None:
                        inputs.pop(key.replace("_", "-"), None)
                    else:
                        inputs[key.replace("_", "-")] = value
    workflows["release-artifacts.yml"] = planted
    problems = _python_problems(workflows, actions)
    assert problems and all("keeps uv's cache on the release path" in p for p in problems), problems


def _with_step_input(workflow: dict, **given: object) -> dict:
    """*workflow* with each direct setup-uv step's ``with:`` given *given* as well."""
    planted = copy.deepcopy(workflow)
    for job in (planted.get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
                step.setdefault("with", {}).update(
                    {key.replace("_", "-"): value for key, value in given.items()}
                )
    return planted


def test_a_release_build_that_caches_managed_python_fails():
    workflows, actions = _workflows(), _actions()
    workflows["release-artifacts.yml"] = _with_step_input(
        workflows["release-artifacts.yml"], enable_cache=True, cache_python=True
    )
    problems = _python_problems(workflows, actions)
    assert problems and all("release-artifacts.yml" in p for p in problems), problems
    assert any("on the release path" in p for p in problems), problems


def test_a_release_job_that_sets_up_through_the_setup_action_fails():
    """The setup action caches managed Python, so a release workflow job that calls it does."""
    workflows, actions = _workflows(), _actions()
    call = {"uses": _ACTION_NAME, "with": {"python-version": "3.12.15"}}
    workflows["release.yml"] = copy.deepcopy(workflows["release.yml"])
    workflows["release.yml"]["jobs"]["planted"] = {"runs-on": "ubuntu-24.04", "steps": [call]}
    problems = _python_problems(workflows, actions)
    assert any(p.startswith("release.yml planted") and "release path" in p for p in problems)


def test_a_workflow_a_release_calls_is_on_its_path():
    workflows = {
        "release-x.yml": {"jobs": {"build": {"uses": "./.github/workflows/inner.yml"}}},
        "inner.yml": {"jobs": {"deep": {"uses": "./.github/workflows/deeper.yml"}}},
        "deeper.yml": {"jobs": {}},
        "tagged.yml": {"on": {"push": {"tags": ["v*"]}}, "jobs": {}},
        "merge.yml": {"on": {"push": {"branches": ["main"]}}, "jobs": {}},
        # A rehearsal tag starts a test of the candidate, not a release.
        "breadth.yml": {"on": {"push": {"tags": ["rehearsal-v*"]}}, "jobs": {}},
    }
    assert release_path(workflows) == {"release-x.yml", "inner.yml", "deeper.yml", "tagged.yml"}


@pytest.mark.parametrize(
    ("inputs", "shown"),
    [
        ({"cache_python": True, "enable_cache": None}, "enable-cache: auto"),
        ({"cache_python": "true", "enable_cache": "auto"}, "enable-cache: auto"),
        ({"cache_python": True, "enable_cache": False}, "enable-cache: false"),
    ],
    ids=["enable-cache left out", "enable-cache auto", "enable-cache false"],
)
def test_managed_python_cached_without_uv_s_own_cache_on_fails(inputs, shown):
    problems = python_caches("planted.yml", _planted(prune_cache=True, **inputs))
    assert any(shown in p and "caches managed Python" in p for p in problems), problems


def test_managed_python_cached_beside_uv_s_cache_passes():
    planted = _planted(enable_cache=True, prune_cache=True, cache_python=True)
    assert python_caches("planted.yml", planted) == []
