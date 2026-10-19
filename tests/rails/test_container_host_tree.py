"""The documented container commands are the ones the product prints, held against the tree.

The tree half of ``tests/test_container_host.py``: README.md's ``docker run``,
docs/guides/containers.md's update blocks, the compose file and the backend Dockerfile, each held
to what ``personalclaw.container_host`` spells, so a documented command and the one the product
prints cannot drift apart again. It reads the tree, so it is a rail: it runs once per CI run, in
the rails job. The commands themselves are tested in the suite module named above.
"""

from __future__ import annotations

import re
import shlex

import pytest
import source_index
import yaml
from rails_repo import REPO

from personalclaw import container_host
from tools import docker_single_container_smoke as smoke

_README = REPO / "README.md"
_GUIDE = REPO / "docs" / "guides" / "containers.md"
_COMPOSE = REPO / "deploy" / "compose" / "compose.yaml"


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


def _compose() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


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
    guide = source_index.text(_GUIDE)
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
    env = _compose()["services"][container_host.COMPOSE_SERVICE]["environment"]
    assert env[container_host.STARTED_BY_ENV] == "compose"


def test_the_names_the_commands_use_are_the_ones_the_installs_create() -> None:
    compose = _compose()
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

    dockerfile = (REPO / "deploy" / "docker" / "Dockerfile.backend").read_text(encoding="utf-8")
    declared = re.findall(r"\bPERSONALCLAW_PORT=(\d+)", dockerfile)
    assert declared == [str(_DEFAULT_PORT)], declared
    argv = shlex.split(container_host.run_command("0.2"))
    assert argv[argv.index("-p") + 1] == f"127.0.0.1:{_DEFAULT_PORT}:{_DEFAULT_PORT}"


def test_the_restart_policy_named_is_the_one_the_install_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(container_host.STARTED_BY_ENV, raising=False)
    assert "--restart unless-stopped" in container_host.keeps_running()
    assert "--restart unless-stopped" in smoke.readme_docker_run(_README)
    monkeypatch.setenv(container_host.STARTED_BY_ENV, "compose")
    assert "restart: unless-stopped" in container_host.keeps_running()
    services = _compose()["services"]
    assert services[container_host.COMPOSE_SERVICE]["restart"] == "unless-stopped"
