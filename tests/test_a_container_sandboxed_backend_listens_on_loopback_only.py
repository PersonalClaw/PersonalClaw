"""An app backend in the container sandbox tier is published on this computer's loopback only.

The tier maps the backend's port to the host so the gateway can reach it. It mapped it as
``-p <port>:<port>``, with no host address, and a container runtime then publishes the port on
every interface of the host: a device on the same network could reach the sandboxed backend
directly, past the gateway's sign-in, the app's declared permissions and the audit. Each mapping
now names ``127.0.0.1``, the address the gateway dials (``RunningBackend.base_url``).
"""

from __future__ import annotations

import re

from personalclaw.apps.backend_runtime import RunningBackend, build_backend_sandbox_spec
from personalclaw.sandbox import ResourceCeilings
from personalclaw.sandbox_providers.base import SandboxSpec
from personalclaw.sandbox_providers.docker import build_docker_argv


def _argv(spec: SandboxSpec) -> list[str]:
    return build_docker_argv(
        ["python", "-m", "backend"],
        workspace_dir="/apps/x",
        ceilings=ResourceCeilings(nofile=1024, max_pids=0, max_rss_mb=0),
        spec=spec,
        container_name="pclaw-sbx-test",
    )


def _published(argv: list[str]) -> list[str]:
    """Every ``-p`` mapping in *argv*, as given."""
    return [argv[i + 1] for i, arg in enumerate(argv) if arg in ("-p", "--publish")]


def test_each_port_is_published_on_loopback_only():
    """🔴 Red before: ``-p 8765:8765``, published on every interface."""
    assert _published(_argv(SandboxSpec(expose_ports=(8765, 9000)))) == [
        "127.0.0.1:8765:8765",
        "127.0.0.1:9000:9000",
    ]


def test_no_mapping_leaves_the_host_address_out():
    """No ``<port>:<port>`` without a host address, nor a wildcard one, anywhere in the argv."""
    argv = _argv(SandboxSpec(expose_ports=(8765,)))
    for mapping in _published(argv):
        assert not re.fullmatch(r"\d+:\d+", mapping), mapping
        assert not mapping.startswith(("0.0.0.0:", "[::]:", ":")), mapping
    assert not any(arg.startswith("--network=host") or arg == "host" for arg in argv)


def test_an_app_backend_declaring_the_tier_is_published_where_the_gateway_dials_it():
    """🔴 Red before. End to end from the backend's spec: its one mapping is on the address the
    gateway reaches it at."""
    spec = build_backend_sandbox_spec(
        workspace_dir="/apps/x", data_dir=None, env={}, port=8000, can_network=False
    )
    (mapping,) = _published(_argv(spec))
    dialed = RunningBackend(name="x", port=8000, pid=0).base_url
    assert dialed == "http://127.0.0.1:8000"
    assert mapping == "127.0.0.1:8000:8000"


def test_no_port_is_published_when_none_is_asked_for():
    assert _published(_argv(SandboxSpec())) == []
