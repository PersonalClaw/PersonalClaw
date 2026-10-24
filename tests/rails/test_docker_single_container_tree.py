"""The source-tree checks split from
``tests/test_docker_single_container.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
import yaml
from docker_single_container_shared import _REPO, get_handler

from tools import docker_single_container_smoke as smoke

_WORKFLOW = _REPO / ".github" / "workflows" / "docker-single-container.yml"


_RELEASE_WORKFLOW = _REPO / ".github" / "workflows" / "release.yml"
#: Where the release builds and smokes its images: release.yml calls it as `artifacts`.
_ARTIFACTS_WORKFLOW = _REPO / ".github" / "workflows" / "release-artifacts.yml"


#: The release job that must prove the image serves a dashboard before anything is pushed.
_RELEASE_GATE_JOB = "gateway-dashboard"


#: The local tag that job loads the layout's linux/amd64 image as.
_RELEASE_GATE_IMAGE = "personalclaw-gateway:release-dashboard"


def test_workflow_runs_the_smoke_tool_against_the_image_it_built() -> None:
    """It must not quietly test `:latest` — that answers about a past release, not this diff.
    The smoke runs the build's own image, by the content ID the build wrote, so no tag can stand
    in."""
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    runs = [str(s.get("run", "")) for s in workflow["jobs"]["single-container"]["steps"]]
    assert (
        "docker build --file deploy/docker/Dockerfile.backend --target runtime"
        ' --iidfile "$RUNNER_TEMP/gateway-image.id" .'
    ) in runs
    assert (
        "python3 tools/docker_single_container_smoke.py"
        ' --image-id-file "$RUNNER_TEMP/gateway-image.id"'
    ) in runs


def _release_jobs(path: Path = _ARTIFACTS_WORKFLOW) -> dict[str, object]:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
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


def test_release_asserts_the_dashboard_of_the_image_it_ships() -> None:
    """The gate exists, builds nothing, and drives the shared smoke tool on the linux/amd64 image
    loaded out of the layout `images` built, after holding that layout's index to the one
    `images` read: the index the release manifest records and release.yml pushes (#3917).
    """
    job = _release_jobs().get(_RELEASE_GATE_JOB)
    assert isinstance(job, dict), (
        f"release-artifacts.yml has no {_RELEASE_GATE_JOB!r} job, so nothing at release time "
        "starts the "
        "gateway — the `--entrypoint ''` smoke proves the binary exists, not the dashboard"
    )
    downloads = [
        (step.get("with") or {}).get("name")
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/download-artifact@")
    ]
    assert downloads == ["image-layout-gateway", "image-index-gateway"], downloads
    script = _run_scripts(job)
    assert "docker build" not in script and "buildx" not in script, "the gate builds its own image"
    assert 'tools/ci/oci_layout.py "$LAYOUT"' in script
    assert 'cmp "$INDEX" "$RUNNER_TEMP/again/personalclaw-gateway.index.json"' in script
    assert '--override-arch amd64 "oci-archive:$LAYOUT"' in script
    smoke = next(s for s in job["steps"] if "docker_single_container_smoke" in str(s.get("run")))
    assert smoke["run"] == 'python3 tools/docker_single_container_smoke.py --image "$IMAGE"'
    assert smoke["env"] == {"IMAGE": "${{ steps.load.outputs.id }}"}, (
        "the gate must smoke the image it loaded, by its content ID; without --image the tool "
        "runs the README's own :latest and reports on a past release"
    )


def test_a_dashboardless_image_is_never_published_or_released() -> None:
    """The wiring IS the gate. A warning, or an assertion after the push, is not one.

    A dashboard assertion added *after* the push would leave a broken `:latest` in GHCR —
    the tag README.md's one-liner points at — with no GitHub Release to explain it. The gate
    smokes the layout `images` built, so it needs `images`; release.yml's `images` (the push)
    needs `artifacts`, which holds both, and `notes` (the GitHub Release) still needs `images`,
    so the whole chain refuses.
    """
    jobs = _release_jobs()
    assert "images" in _needs(jobs.get(_RELEASE_GATE_JOB)), (
        f"{_RELEASE_GATE_JOB!r} does not need `images`, so it cannot smoke the layout that "
        "release.yml pushes"
    )
    release = _release_jobs(_RELEASE_WORKFLOW)
    assert "artifacts" in _needs(
        release.get("images")
    ), "release.yml's image push must wait on `artifacts`, where the dashboard gate runs"
    assert "images" in _needs(release.get("notes")), (
        "the GitHub Release must stay downstream of `images`; without that edge the dashboard "
        "gate cannot block the release"
    )


#: What builds an image, or fetches one from a registry, in a run: line.
_BUILDS_OR_PULLS = re.compile(
    r"\b(?:docker|podman)\s+(?:buildx\s+)?(?:build|bake|pull)\b|\bbuildah\b|\bcrane\s+pull\b"
)
_BUILD_ACTIONS = ("docker/build-push-action@", "docker/bake-action@")


def _builds_or_pulls(run: str) -> bool:
    """A run: block that builds an image, or copies one out of a registry: a `skopeo copy`
    whose source (the next to last word: options come first) is `docker://`. A copy to one,
    release.yml's push, is not a pull."""
    if _BUILDS_OR_PULLS.search(run):
        return True
    for line in run.replace("\\\n", " ").splitlines():
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            continue
        if "skopeo" in words and "copy" in words[words.index("skopeo") :]:
            rest = words[words.index("copy", words.index("skopeo")) + 1 :]
            if len(rest) >= 2 and rest[-2].startswith("docker://"):
                return True
    return False


def _release_builds(texts: dict[str, str]) -> list[str]:
    """Each place release.yml and release-artifacts.yml build an image or fetch one by a tag."""
    found: list[str] = []
    for name, text in texts.items():
        for job_id, job in (yaml.safe_load(text).get("jobs") or {}).items():
            for step in (job or {}).get("steps") or []:
                where = f"{name}:{job_id}"
                if str(step.get("uses", "")).startswith(_BUILD_ACTIONS):
                    found.append(f"{where} builds with {step['uses'].split('@')[0]}")
                if _builds_or_pulls(str(step.get("run", ""))):
                    found.append(f"{where} builds or pulls in a run: line")
    return found


def _release_texts() -> dict[str, str]:
    return {
        "release.yml": _RELEASE_WORKFLOW.read_text(encoding="utf-8"),
        "release-artifacts.yml": _ARTIFACTS_WORKFLOW.read_text(encoding="utf-8"),
    }


def _release_build_problems(texts: dict[str, str]) -> list[str]:
    bad: list[str] = []
    builds = _release_builds(texts)
    if builds != ["release-artifacts.yml:images builds with docker/build-push-action"]:
        bad.append(f"the release builds its images other than once, in `images`: {builds}")
    images = yaml.safe_load(texts["release-artifacts.yml"])["jobs"]["images"]
    for step in images["steps"]:
        if str(step.get("uses", "")).startswith("docker/build-push-action@"):
            given = step.get("with") or {}
            if given.get("no-cache") is not True:
                bad.append("the release's image build may read a cache: it sets no no-cache")
            if {"cache-from", "cache-to"} & set(given) or "type=gha" in str(given):
                bad.append("the release's image build names a cache")
    return bad


def test_the_release_builds_each_image_once_with_no_cache() -> None:
    """`images` builds both images once, clean, and nothing after it builds or pulls by a tag:
    `gateway-dashboard` and release.yml's push read the layout it built (#3917)."""
    assert _release_build_problems(_release_texts()) == []


_DASHBOARD_LOAD = '          rm "$LAYOUT"\n'


@pytest.mark.parametrize(
    "name, old, new, says",
    [
        (
            "release-artifacts.yml",
            _DASHBOARD_LOAD,
            _DASHBOARD_LOAD + '          docker build --target runtime --tag "$IMAGE" .\n',
            "gateway-dashboard builds or pulls",
        ),
        (
            "release-artifacts.yml",
            _DASHBOARD_LOAD,
            _DASHBOARD_LOAD
            + "          docker pull ghcr.io/personalclaw/personalclaw-gateway:latest\n",
            "gateway-dashboard builds or pulls",
        ),
        (
            "release-artifacts.yml",
            _DASHBOARD_LOAD,
            _DASHBOARD_LOAD
            + "          skopeo copy docker://ghcr.io/personalclaw/personalclaw-gateway:latest"
            + ' "docker-archive:/tmp/x.tar"\n',
            "gateway-dashboard builds or pulls",
        ),
        (
            "release-artifacts.yml",
            "          # Clean, for provenance: it reads no cache (#3917).\n"
            "          no-cache: true\n",
            "",
            "sets no no-cache",
        ),
        (
            "release-artifacts.yml",
            "          no-cache: true\n",
            "          no-cache: true\n          cache-from: type=gha,scope=gateway-runtime\n",
            "names a cache",
        ),
    ],
)
def test_a_planted_rebuild_or_cache_in_the_release_fails(
    name: str, old: str, new: str, says: str
) -> None:
    texts = _release_texts()
    assert texts[name].count(old) == 1, f"the plant's anchor is not once: {old!r}"
    texts[name] = texts[name].replace(old, new)
    assert any(says in p for p in _release_build_problems(texts)), _release_build_problems(texts)


def test_a_build_step_added_to_release_yml_fails() -> None:
    texts = _release_texts()
    workflow = yaml.safe_load(texts["release.yml"])
    job = workflow["jobs"]["images"]
    job["steps"].append({"uses": "docker/build-push-action@0000", "with": {"push": True}})
    texts["release.yml"] = yaml.safe_dump(workflow)
    assert any("release.yml:images builds with" in p for p in _release_build_problems(texts))


def test_the_version_only_arch_smoke_is_left_intact() -> None:
    """The additive half, pinned: the new gate must not be bought by weakening the old step.

    `--entrypoint ''` + `personalclaw --version` is a REAL assertion — it is the per-arch
    liveness proof, and arm64's only execution evidence, since the dashboard gate runs the
    runner's native amd64 only. It answers a different question and stays exactly as it is.
    """
    script = _run_scripts(_release_jobs().get("images"))
    assert "--entrypoint ''" in script
    # Each arch is loaded from the layout release.yml pushes, so the smoke runs what ships.
    assert '--override-arch "$arch" "oci-archive:$LAYOUT"' in script
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
    release. The table is held to the router here as well, on every PR, with no image built: it is
    the seven, and each is a GET route the gateway serves. GET, so the step only reads.

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
