"""A refused localhost in the container names an address the user's runtime really gives the host.

The hint used to say "use host.docker.internal (Docker) or host.lima.internal (Lima, Colima,
Finch)". In a Finch container on a Mac, those names resolve only through Lima's user-network DNS
server; the container's other DNS server answers NXDOMAIN for both, and a run found neither name
resolvable, so a user who followed the hint was sent to a host name that did not resolve. The
address that network gives the host, 192.168.5.2, reaches the host's services (loopback-only ones
included) without any DNS. The hint now names that address for Finch and Lima, and the runtimes'
own documented names for Docker Desktop and Podman, and ``docs/guides/containers.md`` says the
same thing.
"""

from __future__ import annotations

from pathlib import Path

from personalclaw.providers.failure_copy import CONTAINER_HOST_ADDRESSES, CONTAINER_LOCALHOST_HINT

_GUIDE = Path(__file__).resolve().parents[1] / "docs" / "guides" / "containers.md"


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_every_runtime_is_named_with_its_own_address() -> None:
    runtimes = dict(CONTAINER_HOST_ADDRESSES)
    assert runtimes == {
        "Docker Desktop": "host.docker.internal",
        "Podman": "host.containers.internal",
        "Finch or Lima on a Mac": "192.168.5.2",
    }
    for runtime, address in CONTAINER_HOST_ADDRESSES:
        assert f"{address} with {runtime}" in CONTAINER_LOCALHOST_HINT


def test_the_hint_names_no_host_name_that_depends_on_one_dns_server() -> None:
    assert "host.lima.internal" not in CONTAINER_LOCALHOST_HINT
    assert "Colima" not in CONTAINER_LOCALHOST_HINT  # never measured, so never claimed


def test_docker_on_linux_is_told_the_flag_and_the_listen_address() -> None:
    """Docker on Linux has no name for the host unless the container is started with one, and
    reaches only a server that listens on more than loopback."""
    assert "--add-host=host.docker.internal:host-gateway" in CONTAINER_LOCALHOST_HINT
    assert "listen on more than localhost" in CONTAINER_LOCALHOST_HINT


def test_the_container_guide_gives_each_runtime_the_same_address() -> None:
    guide = _flat(_GUIDE.read_text(encoding="utf-8"))
    for runtime, address in CONTAINER_HOST_ADDRESSES:
        assert f"`{address}` with {runtime}" in guide, (runtime, address)
    assert "--add-host=host.docker.internal:host-gateway" in guide
    assert "host.lima.internal" not in guide
