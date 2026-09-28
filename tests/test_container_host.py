"""The container install's host commands come from one place, and the docs say the same thing.

The update instructions a container install showed named ``docker compose … pull`` / ``up -d``,
while README.md's container install is a plain ``docker run``: a user who followed the README
was told to run a compose file they never had. ``personalclaw.container_host`` now spells every
host command the product prints for a container, and these tests hold the documented commands
to it (README.md's ``docker run``, docs/guides/containers.md's install, update and recreate
blocks) in both directions, so the two cannot drift apart again.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
import yaml

from personalclaw import container_host
from tools import docker_single_container_smoke as smoke

_REPO = Path(__file__).resolve().parents[1]
_README = _REPO / "README.md"
_GUIDE = _REPO / "docs" / "guides" / "containers.md"
_COMPOSE = _REPO / "deploy" / "compose" / "compose.yaml"


@pytest.fixture
def run_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """A container the README's ``docker run`` started: the compose marker is unset."""
    monkeypatch.delenv(container_host.STARTED_BY_ENV, raising=False)


@pytest.fixture
def compose_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(container_host.STARTED_BY_ENV, "compose")


def _fenced_blocks(text: str) -> list[list[str]]:
    """Each fenced code block's lines, with backslash continuations joined."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("```"):
            if current is None:
                current = []
            else:
                joined = re.sub(r"\s*\\\n\s*", " ", "\n".join(current))
                blocks.append([ln for ln in joined.splitlines() if ln.strip()])
                current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


# ── one source: the documented commands ARE the product's ─────────────────────────────────


def test_the_readmes_docker_run_is_the_one_the_product_prints() -> None:
    """README.md's install is the product's own. ``test_getting_started_walkthrough`` holds the
    other three surfaces that state it (the guides and the website installer) byte-equal to
    README.md's, so all four are this command."""
    assert smoke.readme_docker_run(_README) == container_host.run_command("latest")


def test_the_guides_update_blocks_are_what_the_product_prints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guide's update blocks are ``update_commands`` for each install. The product's first
    line is a shell comment the guide says in prose, and the guide names the one container's
    recreate in words, because each surface states the install's ``docker run`` once."""
    guide = _GUIDE.read_text(encoding="utf-8")
    blocks = _fenced_blocks(guide)
    monkeypatch.delenv(container_host.STARTED_BY_ENV, raising=False)
    *remove, recreate = container_host.update_commands("0.2")[1:]
    assert remove in blocks
    assert recreate == container_host.run_command("0.2")
    assert "`docker run`, with `:0.2` in place of `:latest`" in " ".join(guide.split())
    monkeypatch.setenv(container_host.STARTED_BY_ENV, "compose")
    assert container_host.update_commands("0.2")[1:] in blocks


def test_the_compose_file_says_compose_started_the_container() -> None:
    """Without this, a compose install would be shown the README's ``docker`` commands."""
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    env = compose["services"][container_host.COMPOSE_SERVICE]["environment"]
    assert env[container_host.STARTED_BY_ENV] == "compose"


def test_the_names_the_commands_use_are_the_ones_the_installs_create() -> None:
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    assert container_host.COMPOSE_SERVICE in compose["services"]
    image = compose["services"][container_host.COMPOSE_SERVICE]["image"]
    assert image.split(":", 1)[0] == container_host.IMAGE
    argv = shlex.split(smoke.readme_docker_run(_README))
    assert argv[argv.index("--name") + 1] == container_host.CONTAINER_NAME
    assert argv[argv.index("-v") + 1] == f"{container_host.VOLUME}:/data"
    assert argv[-1].split(":", 1)[0] == container_host.IMAGE


def test_the_published_port_is_the_images_own_and_only_on_loopback() -> None:
    """``run_command`` publishes the product's default port, on the host's loopback only. The
    image's gateway listens on the ``PERSONALCLAW_PORT`` its Dockerfile sets, which cannot import
    that default, so the two are held equal here: an image that moved its port would otherwise be
    recreated with a mapping to a port nothing in it listens on."""
    from personalclaw.config.loader import _DEFAULT_PORT

    dockerfile = (_REPO / "deploy" / "docker" / "Dockerfile.backend").read_text(encoding="utf-8")
    declared = re.findall(r"\bPERSONALCLAW_PORT=(\d+)", dockerfile)
    assert declared == [str(_DEFAULT_PORT)], declared
    argv = shlex.split(container_host.run_command("0.2"))
    assert argv[argv.index("-p") + 1] == f"127.0.0.1:{_DEFAULT_PORT}:{_DEFAULT_PORT}"


# ── the commands ───────────────────────────────────────────────────────────────────────────


def test_the_readme_install_updates_by_recreating_with_its_own_docker_run(run_install) -> None:
    comment, *commands = container_host.update_commands("0.2")
    assert comment.startswith("# ")
    assert commands == [
        "docker pull ghcr.io/personalclaw/personalclaw-gateway:0.2",
        "docker stop personalclaw",
        "docker rm personalclaw",
        container_host.run_command("0.2"),
    ]
    assert "docker compose" not in "\n".join(commands)


def test_an_unresolved_tag_is_latest_for_the_readme_install(run_install) -> None:
    commands = container_host.update_commands("")
    assert "docker pull ghcr.io/personalclaw/personalclaw-gateway:latest" in commands
    assert commands[-1] == container_host.run_command("latest")


def test_compose_carries_the_tag_on_both_commands(compose_install) -> None:
    """``pull`` and ``up`` run as separate processes, so a tag on only one would let the other
    fall back to compose's ``latest``."""
    comment, *commands = container_host.update_commands("0.2")
    assert comment.startswith("# ")
    assert commands == [
        "PERSONALCLAW_IMAGE_TAG=0.2 docker compose -f deploy/compose/compose.yaml pull",
        "PERSONALCLAW_IMAGE_TAG=0.2 docker compose -f deploy/compose/compose.yaml up -d",
    ]
    assert container_host.update_commands("")[1:] == [
        "docker compose -f deploy/compose/compose.yaml pull",
        "docker compose -f deploy/compose/compose.yaml up -d",
    ]


def test_stop_and_restart_are_the_installs_own(run_install, monkeypatch) -> None:
    assert container_host.stop_command() == "docker stop personalclaw"
    assert container_host.restart_command() == "docker restart personalclaw"
    monkeypatch.setenv(container_host.STARTED_BY_ENV, "compose")
    assert container_host.stop_command() == (
        "docker compose -f deploy/compose/compose.yaml stop personalclaw-gateway"
    )
    assert container_host.restart_command() == (
        "docker compose -f deploy/compose/compose.yaml restart personalclaw-gateway"
    )


def test_the_restart_policy_named_is_the_one_the_install_sets(run_install, monkeypatch) -> None:
    assert "--restart unless-stopped" in container_host.keeps_running()
    assert "--restart unless-stopped" in smoke.readme_docker_run(_README)
    monkeypatch.setenv(container_host.STARTED_BY_ENV, "compose")
    assert "restart: unless-stopped" in container_host.keeps_running()
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    assert compose["services"][container_host.COMPOSE_SERVICE]["restart"] == "unless-stopped"


def test_the_setup_quick_start_is_the_readme_command(capsys) -> None:
    """``setup --mode docker`` printed ``docker compose up -d``, which from anywhere but a
    directory holding a compose file finds nothing to start."""
    from personalclaw.cli_setup import _setup_noninteractive

    assert _setup_noninteractive(mode="docker") is True
    out = capsys.readouterr().out
    assert container_host.run_command() in out
    assert "docker compose" not in out
