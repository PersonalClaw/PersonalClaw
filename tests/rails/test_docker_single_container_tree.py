"""The source-tree checks split from
``tests/test_docker_single_container.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re

import yaml
from docker_single_container_shared import _REPO, get_handler

from tools import docker_single_container_smoke as smoke

_WORKFLOW = _REPO / ".github" / "workflows" / "docker-single-container.yml"


_RELEASE_WORKFLOW = _REPO / ".github" / "workflows" / "release.yml"


#: The release job that must prove the image serves a dashboard before anything is pushed.
_RELEASE_GATE_JOB = "gateway-dashboard"


#: The tag that job builds and hands to the smoke tool.
_RELEASE_GATE_IMAGE = "personalclaw-gateway:release-dashboard"


def test_workflow_runs_the_smoke_tool_against_the_image_it_built() -> None:
    """It must not quietly test `:latest` — that answers about a past release, not this diff."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "docker build" in text
    assert "--file deploy/docker/Dockerfile.backend" in text
    assert (
        "tools/docker_single_container_smoke.py --image personalclaw-gateway:single-container-ci"
        in text
    )
    assert "--tag personalclaw-gateway:single-container-ci" in text


def _release_jobs() -> dict[str, object]:
    workflow = yaml.safe_load(_RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    return jobs


def _needs(job: object) -> list[str]:
    """A job's `needs`, normalised — the key takes a scalar OR a list."""
    assert isinstance(job, dict)
    needs = job.get("needs")
    if isinstance(needs, str):
        return [needs]
    assert isinstance(needs, list), f"job has no usable `needs`: {needs!r}"
    return [str(item) for item in needs]


def _run_scripts(job: object) -> str:
    assert isinstance(job, dict)
    steps = job.get("steps")
    assert isinstance(steps, list)
    return "\n".join(str(step["run"]) for step in steps if isinstance(step, dict) and "run" in step)


def test_release_asserts_the_image_dashboard_against_the_image_it_built() -> None:
    """The gate exists, builds the runtime stage itself, and drives the shared smoke tool.

    Building rather than pulling is load-bearing twice over: a published tag is a PAST
    commit (so a pull answers "did the last release bundle the SPA"), and
    `ghcr-anonymous-pull.yml` already owns the published-artifact question.
    """
    job = _release_jobs().get(_RELEASE_GATE_JOB)
    assert job is not None, (
        f"release.yml has no {_RELEASE_GATE_JOB!r} job, so nothing at release time starts the "
        "gateway — the `--entrypoint ''` smoke proves the binary exists, not the dashboard"
    )
    script = _run_scripts(job)
    assert "docker build" in script
    assert "--file deploy/docker/Dockerfile.backend" in script
    assert "--target runtime" in script
    assert f"--tag {_RELEASE_GATE_IMAGE}" in script
    assert "tools/docker_single_container_smoke.py" in script
    assert f"--image {_RELEASE_GATE_IMAGE}" in script, (
        "the gate must be pointed at the tag it just built; without --image the tool runs the "
        "README's own :latest and reports on a past release"
    )


def test_a_dashboardless_image_is_never_published_or_released() -> None:
    """The wiring IS the gate. A warning, or an assertion after the push, is not one.

    `images` pushes `:X.Y.Z`, `:X.Y` and `:latest` and only THEN smokes what it pushed, so a
    dashboard assertion added *after* that push would leave a broken `:latest` in GHCR —
    the tag README.md's one-liner points at — with no GitHub Release to explain it. Hence
    `images: needs:` the gate, and `notes` (the GitHub Release) still needs `images`, so the
    whole chain refuses.
    """
    jobs = _release_jobs()
    assert _RELEASE_GATE_JOB in _needs(jobs.get("images")), (
        f"the `images` job does not need {_RELEASE_GATE_JOB!r}, so the multi-arch push runs "
        "regardless of the dashboard assertion and a broken `:latest` ships"
    )
    assert "images" in _needs(jobs.get("notes")), (
        "the GitHub Release must stay downstream of `images`; without that edge the dashboard "
        "gate cannot block the release"
    )


def test_the_version_only_arch_smoke_is_left_intact() -> None:
    """The additive half, pinned: the new gate must not be bought by weakening the old step.

    `--entrypoint ''` + `personalclaw --version` is a REAL assertion — it is the per-arch
    liveness proof, and arm64's only execution evidence, since the dashboard gate runs the
    runner's native amd64 only. It answers a different question and stays exactly as it is.
    """
    script = _run_scripts(_release_jobs().get("images"))
    assert "--entrypoint ''" in script
    assert 'docker pull --quiet --platform "linux/$arch"' in script
    assert "for arch in amd64 arm64; do" in script


def test_main_runs_the_later_steps_after_the_dashboard_ones() -> None:
    """A step with no call site guards nothing; asserted on `main`'s AST."""
    import ast

    tree = ast.parse((_REPO / "tools" / "docker_single_container_smoke.py").read_text())
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    order = [
        node.func.id
        for node in sorted(ast.walk(main), key=lambda n: (getattr(n, "lineno", 0), 0))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    for step in (
        "_assert_installed_record",
        "_assert_download_offer",
        "_assert_login_shell_finds_the_agent_clis",
        "_assert_api_endpoints",
    ):
        assert step in order, f"main() never runs {step}: {order}"
        assert order.index("_assert_asset") < order.index(step)
    assert order.index("_assert_download_offer") < order.index("_assert_api_endpoints")


#: The endpoints step 9 reads, spelled out: a table that shrank, grew or renamed one fails here.
_API_STEP_ENDPOINTS = (
    "/api/system",
    "/api/auth-status",
    "/api/providers",
    "/api/models/active",
    "/api/security/credentials",
    "/api/sessions",
    "/api/agents",
)


#: What the check before step 9 read in place of `/api/models/active` and
#: `/api/security/credentials`. The gateway serves neither, so they are the negative control.
_REMOVED_API_ROUTES = ("/api/use-cases", "/api/credentials")


def test_the_api_step_reads_the_seven_get_routes_the_gateway_serves() -> None:
    """Step 9 runs live only where an image is built: this workflow, for its own paths, and the
    release. A route handler removed under `src/personalclaw/dashboard/` is in neither path list,
    so the table is held to the router here, on every PR: it is the seven, and each is a GET route
    the gateway serves. GET, so the step only reads.

    The two removed routes are the control: a resolver that found a handler for them would find
    one for anything, and the check above would pass for any table.
    """
    assert tuple(smoke._API_ENDPOINTS) == _API_STEP_ENDPOINTS, (
        "step 9's endpoints changed; a smaller table checks less at release, so change this "
        f"pin only with the reason: {tuple(smoke._API_ENDPOINTS)}"
    )
    not_served = [path for path in _API_STEP_ENDPOINTS if get_handler(path) is None]
    assert not not_served, f"step 9 reads routes the gateway serves no GET for: {not_served}"
    served = [path for path in _REMOVED_API_ROUTES if get_handler(path) is not None]
    assert not served, f"the resolver finds a handler for a removed route: {served}"


def test_the_image_links_each_command_its_agent_cli_lock_provides() -> None:
    """The image links the agent CLIs' commands into /usr/local/bin by name. A Dependabot bump
    that renames or adds a command moves only the lock, so the link list is held to the commands
    the lock's own tools declare, and a link whose target is missing fails the build: a
    dangling one would leave the gateway fetching the newest release with npx at runtime."""
    dockerfile = (_REPO / "deploy/docker/Dockerfile.backend").read_text(encoding="utf-8")
    loop = re.search(r"for command in ([^;]*); do(.*?)done", dockerfile, re.S)
    assert loop, "the image no longer links the agent CLIs' commands"
    lock = (_REPO / smoke.AGENT_CLIS_LOCK).read_text(encoding="utf-8")
    assert sorted(loop.group(1).split()) == smoke.agent_commands(lock)
    body = " ".join(loop.group(2).replace("\\\n", " ").split())
    assert 'test -x /usr/local/bin/"$command" || exit 1' in body, body
