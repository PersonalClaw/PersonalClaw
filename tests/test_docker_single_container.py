"""The single-container `docker run` one-liner must exist in README.md and be testable — DIST-15.

`DIST-15`'s clause is "ONE command copied verbatim from README.md reaches a USABLE
dashboard". Two halves of that are checkable without a Docker daemon, and they are exactly
the two halves that were measured missing:

* **the command exists, and there is only one.** ``grep -c 'docker run' README.md`` was
  **0** when the atom was written, so the "copied verbatim from README.md" half had nothing
  to copy. This suite is what keeps that from silently regressing in the fast gate, on
  every platform, with no daemon — the rail in
  ``.github/workflows/docker-single-container.yml`` needs a Linux runner and an image build.
* **the SPA is bundled into the image.** ``Dockerfile.backend`` must build ``web/dist`` and
  place it where ``setup.py``'s ``BuildWithWeb._find_web_dist`` looks, BEFORE the
  ``pip install`` that grafts it. Without that the image answers ``/api/healthz`` 200 and
  serves no dashboard — the failure the ``done_when`` calls out by name.

What is deliberately NOT here: the live drive (boot the container, fetch ``/``, fetch the
first bundle). That needs a daemon and belongs to the workflow. Asserting the argv-rewriting
and the Dockerfile's structure is what makes a green run of that workflow mean something —
a rail that retargeted the *volume* spec as if it were the image ref would have tested
whatever ``:latest`` resolves to while reporting on the commit under review.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from personalclaw.dashboard.handlers.core import _UNBUNDLED_PAGE
from tools import docker_single_container_smoke as smoke

_REPO = Path(__file__).resolve().parents[1]
_README = _REPO / "README.md"
_DOCKERFILE = _REPO / "deploy" / "docker" / "Dockerfile.backend"
_WORKFLOW = _REPO / ".github" / "workflows" / "docker-single-container.yml"
_RELEASE_WORKFLOW = _REPO / ".github" / "workflows" / "release.yml"

#: The release job that must prove the image serves a dashboard before anything is pushed.
_RELEASE_GATE_JOB = "gateway-dashboard"
#: The tag that job builds and hands to the smoke tool.
_RELEASE_GATE_IMAGE = "personalclaw-gateway:release-dashboard"

#: A stand-in for the session token the gateway banner prints. Three dot-separated segments
#: so it exercises the same shape a JWT has, but a fixed literal that is not and never was a
#: credential — a fixture copied from a real run would put the very value these tests exist
#: to keep out of a public log INTO the repository.
_SYNTHETIC_TOKEN = "synthetic.placeholder.not-a-real-token"


# ---------------------------------------------------------------------------
# The README command
# ---------------------------------------------------------------------------


def test_readme_carries_exactly_one_docker_run() -> None:
    command = smoke.readme_docker_run(_README)
    assert command.startswith("docker run ")


def test_readme_command_runs_the_published_gateway_image() -> None:
    """A placeholder tag or a locally-built name would not be copy-pasteable."""
    command = smoke.readme_docker_run(_README)
    argv, image = smoke.retarget(command, None, "n", "v")
    assert image.startswith("ghcr.io/personalclaw/personalclaw-gateway:"), image
    tag = image.split(":", 1)[1]
    assert re.fullmatch(r"latest|\d+\.\d+\.\d+", tag), (
        f"tag {tag!r} is a placeholder a reader must edit; the clause says the command is "
        "copied verbatim"
    )
    assert argv[:2] == ["docker", "run"]


def test_readme_command_persists_state_and_publishes_the_port() -> None:
    """The clause names both: a `-v …:/data` volume and a published :10000."""
    command = smoke.readme_docker_run(_README)
    argv, _image = smoke.retarget(command, None, "n", "v")
    assert smoke.published_port(argv) == 10000
    assert any(
        arg.endswith(":/data") for arg in argv
    ), "without a volume at /data the container loses every bit of state on `docker rm`"


def test_readme_command_makes_the_published_port_reachable() -> None:
    """`PERSONALCLAW_BIND_HOST` is load-bearing, not decoration.

    ``origin.resolve_bind_host`` defaults to ``127.0.0.1``, which inside a container is the
    container's own loopback — ``-p`` forwards to it and finds nothing. compose sets this for
    the same reason (``deploy/compose/compose.yaml``: "so port-forwarding works"). A command
    missing it starts a container that never answers, which reads as a broken image.
    """
    command = smoke.readme_docker_run(_README)
    assert "PERSONALCLAW_BIND_HOST=0.0.0.0" in command


def test_readme_command_does_not_disable_auth() -> None:
    """The one-liner must not buy a reachable dashboard by turning the token gate off.

    ``PERSONALCLAW_AUTH_MODE=none`` pins the bind to loopback *unless*
    ``PERSONALCLAW_BIND_HOST`` overrides it — and that pair (no auth on ``0.0.0.0``) is
    exactly what the loopback invariant exists to prevent. ``BYPASS_LOCAL_NETWORKS=1`` is the
    same trade: compose declines to set it by default and documents why. So does this.
    """
    command = smoke.readme_docker_run(_README)
    assert "PERSONALCLAW_AUTH_MODE" not in command
    assert "PERSONALCLAW_BYPASS_LOCAL_NETWORKS" not in command
    assert "PERSONALCLAW_DEV_NO_AUTH" not in command


# ---------------------------------------------------------------------------
# Argv rewriting — what makes the live rail's green mean something
# ---------------------------------------------------------------------------


def test_retarget_rewrites_image_name_and_volume_only() -> None:
    command = (
        "docker run -d --name personalclaw -p 127.0.0.1:10000:10000 "
        "-e PERSONALCLAW_BIND_HOST=0.0.0.0 -v personalclaw_home:/data "
        "ghcr.io/personalclaw/personalclaw-gateway:latest"
    )
    argv, image = smoke.retarget(command, "local:ci", "smoke-name", "smoke_vol")
    assert image == "local:ci"
    assert argv == [
        "docker",
        "run",
        "-d",
        "--name",
        "smoke-name",
        "-p",
        "127.0.0.1:10000:10000",
        "-e",
        "PERSONALCLAW_BIND_HOST=0.0.0.0",
        "-v",
        "smoke_vol:/data",
        "local:ci",
    ]


def test_retarget_does_not_mistake_a_volume_spec_for_the_image() -> None:
    """The trap a pattern-matching scan falls into.

    ``personalclaw_home:/data`` satisfies "contains a slash and a colon and is not a flag",
    so a regex-based scan picks it up as the image ref. The rail then runs the README's own
    ``:latest`` while reporting that it tested the image built from the commit — a green that
    describes the wrong artifact.
    """
    command = (
        "docker run -v personalclaw_home:/data ghcr.io/personalclaw/personalclaw-gateway:0.1.3"
    )
    argv, image = smoke.retarget(command, "local:ci", "n", "smoke_vol")
    assert image == "local:ci"
    assert argv[-1] == "local:ci"
    assert "smoke_vol:/data" in argv


def test_retarget_leaves_bind_mounts_alone() -> None:
    """Only a NAMED volume is renamed; a host path is the reader's own directory."""
    command = "docker run -v /srv/pc:/data ghcr.io/personalclaw/personalclaw-gateway:latest"
    argv, _image = smoke.retarget(command, None, "n", "smoke_vol")
    assert "/srv/pc:/data" in argv


def test_retarget_reads_an_unknown_value_flags_value_as_the_image() -> None:
    """Pins the known limit of the positional scan, so a future reader hits it on purpose.

    An unrecognised value-taking flag has its VALUE read as the image ref. That is why
    ``_VALUE_FLAGS`` is enumerated rather than inferred: the fix for a new flag is to add it
    there, and this test is the note that says so.
    """
    command = "docker run --totally-new-flag some-value ghcr.io/personalclaw/x:latest"
    _argv, image = smoke.retarget(command, None, "n", "v")
    assert image == "some-value", (
        "documents today's behaviour: an unknown flag's value is taken positionally. If this "
        "ever matters for the real command, add the flag to _VALUE_FLAGS."
    )


def test_readme_extraction_is_unmeasurable_when_the_command_is_gone(tmp_path: Path) -> None:
    """Zero commands is the atom's starting state, and it must read as unmeasurable, not pass."""
    readme = tmp_path / "README.md"
    readme.write_text("# No docker here\n\n```bash\npip install personalclaw\n```\n")
    with pytest.raises(smoke.Unmeasurable):
        smoke.readme_docker_run(readme)


def test_readme_extraction_refuses_to_guess_between_two_commands(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(
        "```bash\ndocker run a:1\n```\n\n```bash\ndocker run b:2\n```\n",
    )
    with pytest.raises(smoke.Unmeasurable):
        smoke.readme_docker_run(readme)


def test_readme_extraction_joins_a_backslash_continued_command(tmp_path: Path) -> None:
    """A reader pastes a multi-line command as one; the parser must see it that way too."""
    readme = tmp_path / "README.md"
    readme.write_text("```bash\ndocker run -d \\\n  --name pc \\\n  img:1\n```\n")
    assert smoke.readme_docker_run(readme) == "docker run -d --name pc img:1"


# ---------------------------------------------------------------------------
# The image must actually bundle the SPA
# ---------------------------------------------------------------------------


def test_dockerfile_builds_the_spa() -> None:
    text = _DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^FROM \S*node:\S+ AS web$", text, re.MULTILINE), (
        "Dockerfile.backend needs a Node stage that builds the SPA; without it "
        "setup.py's BuildWithWeb finds no web/dist and the image serves no dashboard"
    )
    assert "npm run build --workspace=web" in text


def test_dockerfile_copies_the_spa_in_before_the_install_that_grafts_it() -> None:
    """Order is the whole contract: ``BuildWithWeb`` reads ``web/dist`` DURING ``pip install``."""
    text = _DOCKERFILE.read_text(encoding="utf-8")
    copy_at = text.find("COPY --from=web /app/web/dist ./web/dist")
    assert copy_at != -1, "the built SPA must land at ./web/dist, where setup.py looks first"
    install_at = text.find("pip install --no-cache-dir --no-deps --no-build-isolation .")
    assert install_at != -1
    assert copy_at < install_at, (
        "the SPA is copied AFTER the install that would have grafted it, so the installed "
        "package carries no static/dist — the image would serve healthz 200 and no dashboard"
    )


def test_spa_stage_matches_the_web_images_recipe() -> None:
    """One SPA build recipe, two images — a drift here means two different dashboards."""
    web_dockerfile = (_REPO / "deploy" / "docker" / "Dockerfile.web").read_text(encoding="utf-8")
    backend = _DOCKERFILE.read_text(encoding="utf-8")
    for line in (
        "COPY package.json package-lock.json ./",
        "COPY web/package.json web/",
        "COPY scripts/install_git_hooks.sh scripts/",
        "RUN npm ci --workspace=web --prefer-offline",
        "COPY src/personalclaw/scan_rule_gloss.json src/personalclaw/",
        "RUN NODE_OPTIONS=--max-old-space-size=4096 npm run build --workspace=web",
    ):
        assert line in web_dockerfile, f"{line!r} moved in Dockerfile.web — retarget this test"
        assert line in backend, f"Dockerfile.backend's web stage is missing {line!r}"


def test_spa_stage_reaches_outside_web_before_the_commands_that_need_it() -> None:
    """Membership alone passed while the stage was unbuildable — these two COPYs are ORDERED.

    Both hunks answer a hard `npm` failure rather than a nicety (#3362 measured them against
    Dockerfile.web): the root ``postinstall`` runs during ``npm ci`` and exits 2 without
    ``scripts/``, and ``tsc`` fails TS2307 without the gloss JSON, so a COPY placed after the
    command it feeds is exactly as broken as a missing one.
    """
    backend = _DOCKERFILE.read_text(encoding="utf-8")
    for copy_line, needs_it in (
        ("COPY scripts/install_git_hooks.sh scripts/", "RUN npm ci --workspace=web"),
        (
            "COPY src/personalclaw/scan_rule_gloss.json src/personalclaw/",
            "RUN NODE_OPTIONS=--max-old-space-size=4096 npm run build --workspace=web",
        ),
    ):
        copy_at = backend.find(copy_line)
        command_at = backend.find(needs_it)
        assert copy_at != -1, f"web stage is missing {copy_line!r}"
        assert command_at != -1, f"{needs_it!r} moved — retarget this test"
        assert copy_at < command_at, (
            f"{copy_line!r} comes after {needs_it!r}, so npm never sees it and the SPA "
            "stage fails before vite — the image would carry no dashboard"
        )


# ---------------------------------------------------------------------------
# The workflow
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The log dump must not print the session token into a public CI log
# ---------------------------------------------------------------------------
#
# The container's own stdout carries a live session token: `gateway.py` mints one at startup
# and `dashboard/origin.py`'s `format_dashboard_urls` prints the authenticated dashboard URL
# (`   http://localhost:10000?token=…`). `_dump_container_logs` then echoes 40 lines of that
# stdout into a GitHub Actions log — on a PUBLIC repo, on every push to `main` touching
# `README.md` / `web/**` / the Dockerfile, and on a job whose greenness means nobody reads it.
#
# These rails drive the print path with `_docker` faked, so they need no Docker daemon and no
# real credential. They deliberately assert on what reaches stdout rather than grepping the
# source for a call: the bug being fixed was NOT a missing pattern (`_TOKEN_RE` has been in
# this file all along, to extract the token) — it was a print site that did not use one, and
# only driving the print site can tell those two apart.


def _fake_docker_logs(
    monkeypatch: pytest.MonkeyPatch, *, stdout: str = "", stderr: str = ""
) -> None:
    """Make `_docker("logs", …)` return a canned tail, so no daemon is needed."""

    def _fake(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        assert args[0] == "logs", f"unexpected docker call {args!r}"
        return subprocess.CompletedProcess(
            args=list(args), returncode=0, stdout=stdout, stderr=stderr
        )

    monkeypatch.setattr(smoke, "_docker", _fake)


def test_log_dump_redacts_the_startup_banners_session_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The banner line is the measured leak: its token must not reach the public log."""
    banner = (
        "PersonalClaw gateway starting…\n"
        "Dashboard:\n"
        f"   http://localhost:10000?token={_SYNTHETIC_TOKEN}\n"
    )
    _fake_docker_logs(monkeypatch, stdout=banner)

    smoke._dump_container_logs("pc-smoke")
    out = capsys.readouterr().out

    assert _SYNTHETIC_TOKEN not in out, (
        "the container's session token reached stdout, which in CI is a world-readable log "
        "on a public repo"
    )
    assert "?token=<redacted>" in out, (
        "the token span must be replaced by a marker, not deleted — the dump exists so a "
        "reader can see what the container said, including THAT a token was printed"
    )
    # The rest of the tail, and the fold that makes it readable, must survive intact.
    assert "PersonalClaw gateway starting…" in out
    assert "http://localhost:10000" in out
    assert "::group::" in out and "::endgroup::" in out


def test_log_dump_leaves_a_tail_with_no_token_byte_identical(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Negative control: a redactor that mangles ordinary log lines is a different bug."""
    tail = (
        "PersonalClaw gateway starting…\n"
        "12:00:00 WARNING personalclaw.gateway: Background session starting\n"
        "GET /api/healthz 200\n"
    )
    _fake_docker_logs(monkeypatch, stdout=tail, stderr="tini: reaped 1 child\n")

    smoke._dump_container_logs("pc-smoke")
    out = capsys.readouterr().out

    assert out == (
        "::group::container logs (last 40 lines, session tokens redacted)\n"
        f"{tail}tini: reaped 1 child\n"
        "\n"
        "::endgroup::\n"
    ), "text carrying no token must pass through unchanged, byte for byte"
    assert "<redacted>" not in out


def test_redaction_covers_both_query_separators_and_every_occurrence() -> None:
    """`?token=` and `&token=` are the two shapes `_TOKEN_RE` claims; one pass must do both.

    Two occurrences, not one: `re.sub` scrubs every match, and a banner plus an echoed
    request line can easily put the same token on the tail twice.
    """
    text = (
        f"   http://localhost:10000?token={_SYNTHETIC_TOKEN}\n"
        f"   http://localhost:10000/?foo=1&token={_SYNTHETIC_TOKEN}&bar=2\n"
    )
    scrubbed = smoke.redact_secrets(text)

    assert _SYNTHETIC_TOKEN not in scrubbed
    assert scrubbed == (
        "   http://localhost:10000?token=<redacted>\n"
        "   http://localhost:10000/?foo=1&token=<redacted>&bar=2\n"
    ), "the separator and the neighbouring query params must survive; only the value goes"


def test_mint_token_failure_scrubs_the_stderr_it_echoes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other site that echoes captured container output into the same public log.

    `_mint_token` only reaches its failure message when `_TOKEN_RE` found nothing in
    **stdout** — so stdout is provably clean there — but it echoes **stderr** too, and stderr
    is never searched. Same log, same exposure, so the same scrub.
    """

    def _fake(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=1,
            stdout="no url here\n",
            stderr=f"traceback printed the url http://localhost:10000?token={_SYNTHETIC_TOKEN}\n",
        )

    monkeypatch.setattr(smoke, "_docker", _fake)

    with pytest.raises(SystemExit) as excinfo:
        smoke._mint_token("pc-smoke")
    assert excinfo.value.code == 1

    captured = capsys.readouterr()
    assert _SYNTHETIC_TOKEN not in captured.err + captured.out
    assert "token=<redacted>" in captured.err


def test_workflow_runs_the_smoke_tool_against_the_image_it_built() -> None:
    """It must not quietly test `:latest` — that answers about a past release, not this diff."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "docker build" in text
    assert "--file deploy/docker/Dockerfile.backend" in text
    assert "tools/docker_single_container_smoke.py --image personalclaw-gateway:dist15-ci" in text
    assert "--tag personalclaw-gateway:dist15-ci" in text


# ---------------------------------------------------------------------------
# The RELEASE workflow — the PR rail's assertion had no release-time equivalent
# ---------------------------------------------------------------------------
#
# 🔴 THE MEASURED GAP. `release.yml`'s gateway smoke runs `personalclaw --version` with
# `--entrypoint ''`, so the gateway never starts: it proves the binary exists, not that the
# product works. Measured 2026-09-23 in containers against the PUBLISHED 0.1.3 image:
# `/api/healthz` → 200 `{"status": "ok"}` while `GET /` → **503** with the
# "Build the dashboard" placeholder and ZERO asset references — and the same version installed
# via `uv tool install personalclaw` served the real shell with a 6.9 MB bundle. So the wheel
# could not ship broken (`scripts/verify_wheel.py` is a release gate) and the image could.
#
# These rails hold the closing of that asymmetry in place. They assert the WIRING (a gate that
# does not block is not a gate) and, below, the two discriminators that make its green mean
# something — both drivable with no Docker daemon.


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


# ---------------------------------------------------------------------------
# The two discriminators, driven with no daemon
# ---------------------------------------------------------------------------
#
# A status is not the assertion. The SPA-less image answers `/` with a real HTTP response and
# `/api/healthz` with 200 `ok`, and the SPA's index.html fallback answers ANY unknown path 200.
# So both discriminators are pinned here against the page the product actually serves —
# imported, not re-typed, so a reworded placeholder cannot leave these green.


def _fake_http(monkeypatch: pytest.MonkeyPatch, routes: dict[str, tuple[int, str, str]]) -> None:
    """Route `_get` by URL suffix match, so no container and no daemon are needed."""

    def _fake(url: str) -> tuple[int, str, str]:
        for suffix, response in routes.items():
            if suffix in url:
                return response
        return 404, "application/octet-stream", "not found"

    monkeypatch.setattr(smoke, "_get", _fake)


def test_the_shell_assertion_rejects_the_page_an_spa_less_image_serves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The measured 503 placeholder, at its real status AND at 200.

    Both, because the status is not what makes it wrong: a 503 is a valid HTTP response and a
    placeholder served 200 is the same non-dashboard. The assertion has to reject the BODY.
    """
    for status in (503, 200):
        _fake_http(monkeypatch, {"/?token=": (status, "text/html", _UNBUNDLED_PAGE)})
        with pytest.raises(SystemExit) as excinfo:
            smoke._assert_shell("http://127.0.0.1:1", _SYNTHETIC_TOKEN)
        assert excinfo.value.code == 1, f"status {status} must FAIL, not be unmeasurable"


def test_the_placeholder_page_carries_neither_discriminator() -> None:
    """Why the rail above can distinguish them at all — and it is not a given.

    `_UNBUNDLED_PAGE` tells a reader to run `cd web && npm install`, which is impossible inside
    the image; if it ever grew a root div or a script tag, `_assert_shell` would wave it
    through at 200. This is the vacuity floor for the test above, in the same file.
    """
    assert smoke._SHELL_MARKER not in _UNBUNDLED_PAGE
    assert not smoke._SCRIPT_RE.findall(_UNBUNDLED_PAGE)


def test_the_shell_assertion_accepts_a_real_shell_and_returns_its_first_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control: a negated assertion that cannot pass is indistinguishable from a bug."""
    shell = (
        f"<!doctype html><html><body>{smoke._SHELL_MARKER}></div>"
        '<script type="module" src="/assets/index-abc123.js"></script></body></html>'
    )
    _fake_http(monkeypatch, {"/?token=": (200, "text/html", shell)})
    assert smoke._assert_shell("http://127.0.0.1:1", _SYNTHETIC_TOKEN) == "/assets/index-abc123.js"


def test_the_asset_assertion_rejects_the_shell_served_in_place_of_a_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200 is not enough, and this is exactly why: the fallback answers with HTML."""
    _fake_http(monkeypatch, {"/assets/": (200, "text/html; charset=utf-8", "<!doctype html>")})
    with pytest.raises(SystemExit) as excinfo:
        smoke._assert_asset("http://127.0.0.1:1", "/assets/index-abc123.js")
    assert excinfo.value.code == 1


def test_the_asset_assertion_is_unmeasurable_when_a_fabricated_asset_is_also_javascript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A catch-all that serves everything as JS makes the check above pass for any input.

    That is not a green and not a product failure — it is an unmeasurable run (exit 2), and
    the distinction is the difference between "the image is fine" and "this rail is vacuous".
    """
    _fake_http(monkeypatch, {"/assets/": (200, "text/javascript", "console.log(1)")})
    with pytest.raises(smoke.Unmeasurable):
        smoke._assert_asset("http://127.0.0.1:1", "/assets/index-abc123.js")


def test_the_asset_assertion_accepts_a_real_bundle_with_the_control_firing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The passing shape, end to end: a JS bundle plus a fabricated path that 404s."""
    _fake_http(
        monkeypatch,
        {
            "/assets/index-abc123.js": (200, "text/javascript", "export const a=1"),
            "/assets/dist15-control-": (404, "application/octet-stream", "not found"),
        },
    )
    smoke._assert_asset("http://127.0.0.1:1", "/assets/index-abc123.js")
