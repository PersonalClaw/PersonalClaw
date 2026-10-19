"""The container install's host commands come from one place, and the docs say the same thing.

The update instructions a container install showed named ``docker compose … pull`` / ``up -d``,
while README.md's container install is a plain ``docker run``: a user who followed the README
was told to run a compose file they never had. ``personalclaw.container_host`` now spells every
host command the product prints for a container. These tests hold the commands it prints;
``tests/rails/test_container_host_tree.py`` holds the documented ones to them (README.md's
``docker run``, docs/guides/containers.md's install, update and recreate blocks, the compose
file), once per CI run in the rails job, so the two cannot drift apart again.
"""

from __future__ import annotations

import pytest

from personalclaw import container_host


@pytest.fixture
def run_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """A container the README's ``docker run`` started: the compose marker is unset."""
    monkeypatch.delenv(container_host.STARTED_BY_ENV, raising=False)


@pytest.fixture
def compose_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(container_host.STARTED_BY_ENV, "compose")


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


def test_the_setup_quick_start_is_the_readme_command(capsys) -> None:
    """``setup --mode docker`` printed ``docker compose up -d``, which from anywhere but a
    directory holding a compose file finds nothing to start."""
    from personalclaw.cli_setup import _setup_noninteractive

    assert _setup_noninteractive(mode="docker") is True
    out = capsys.readouterr().out
    assert container_host.run_command() in out
    assert "docker compose" not in out
