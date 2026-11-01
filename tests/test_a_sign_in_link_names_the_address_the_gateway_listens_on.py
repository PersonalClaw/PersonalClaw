"""Every sign-in link names the exact address the gateway listens on, never a name.

The links PersonalClaw prints or opens to its dashboard (the startup sign-in link, ``personalclaw
token``, setup's dashboard line, and the tunnel hints the banner and the Doctor give) named
``personalclaw.localhost`` or ``localhost``, and a name can resolve to an address the gateway does
not hold. Now one rule decides the host of every one of them (``origin.link_host``): the address
the gateway listens on (``origin.gateway_bind_host``, which the server binds), ``127.0.0.1`` by
default, the loopback address of a wildcard bind's family, and an IPv6 address in brackets. A
``dashboard.url`` the owner configured still wins where it did.

Each case compares a printed link's host with the address the gateway would listen on; no
listener is opened here.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from test_container_url_names_its_port import _SYNTHETIC_TOKEN, _run_token

pytestmark = pytest.mark.security

_ORIGIN = "personalclaw.dashboard.origin"

#: ``PERSONALCLAW_BIND_HOST`` (unset for the default), and the host every link must then name.
BINDS = [
    pytest.param(None, "127.0.0.1", id="the default"),
    pytest.param("0.0.0.0", "127.0.0.1", id="every IPv4 address"),
    pytest.param("10.1.2.3", "10.1.2.3", id="one address"),
    pytest.param("::1", "[::1]", id="IPv6 loopback"),
    pytest.param("::", "[::1]", id="every IPv6 address"),
    pytest.param("fd00::5", "[fd00::5]", id="one IPv6 address"),
]


def _host(url: str) -> str:
    """The host a URL names, brackets kept."""
    netloc = urlsplit(url).netloc
    return netloc.rsplit(":", 1)[0] if not netloc.endswith("]") else netloc


@pytest.fixture
def bound(monkeypatch):
    """Sets the bind the gateway would use, with sign-in on and no SSH session."""

    def bind(value: str | None) -> None:
        if value is None:
            monkeypatch.delenv("PERSONALCLAW_BIND_HOST", raising=False)
        else:
            monkeypatch.setenv("PERSONALCLAW_BIND_HOST", value)
        monkeypatch.delenv("PERSONALCLAW_AUTH_MODE", raising=False)
        monkeypatch.delenv("SSH_CONNECTION", raising=False)
        monkeypatch.delenv("SSH_CLIENT", raising=False)

    return bind


@pytest.mark.parametrize(("bind", "expected"), BINDS)
def test_the_startup_link_names_the_bound_address(bound, bind, expected) -> None:
    from personalclaw.dashboard.origin import (
        build_dashboard_url,
        is_local_bind,
        resolve_bind_host,
        resolve_dashboard_host,
    )

    bound(bind)
    local_only = is_local_bind(resolve_bind_host())
    link = build_dashboard_url(
        f"http://{resolve_dashboard_host(local_only)}:10000", "t", local_only=local_only
    )
    assert _host(link) == expected, link


@pytest.mark.parametrize(("bind", "expected"), BINDS[:3])
def test_the_token_command_names_the_bound_address(
    bound, monkeypatch, tmp_path: Path, capsys, bind, expected
) -> None:
    bound(bind)
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    out, _err, port = _run_token(monkeypatch, tmp_path, capsys)
    assert out == f"http://{expected}:{port}?token={_SYNTHETIC_TOKEN}\n", out


@pytest.mark.parametrize(("bind", "expected"), BINDS[:3])
def test_setups_dashboard_line_names_the_bound_address(bound, capsys, bind, expected) -> None:
    from personalclaw.cli_setup import _maybe_setup_custom_domain

    bound(bind)
    _maybe_setup_custom_domain()
    urls = re.findall(r"https?://\S+", capsys.readouterr().out)
    assert urls and all(_host(url) == expected for url in urls), urls


@pytest.mark.parametrize(("bind", "expected"), BINDS)
def test_the_banners_tunnel_hint_names_the_bound_address(
    bound, monkeypatch, bind, expected
) -> None:
    from unittest.mock import patch

    from personalclaw.dashboard.origin import format_dashboard_urls

    bound(bind)
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 1234 5.6.7.8 5678")
    with (
        patch(f"{_ORIGIN}.devspaces_proxy_url", return_value=None),
        patch(f"{_ORIGIN}.machine_hostname", return_value="box"),
    ):
        lines = format_dashboard_urls("http://127.0.0.1:7777?token=t", port=7777)
    forward = next(line for line in lines if "ssh -L" in line)
    listen, target = re.search(r"ssh -L (\S+):7777:(\S+):7777 box", forward).groups()
    assert (listen, target) == ("127.0.0.1", expected), forward
    opened = next(line for line in lines if "then open" in line)
    assert _host(re.search(r"https?://\S+", opened).group()) == "127.0.0.1", opened


@pytest.mark.parametrize(("bind", "expected"), BINDS)
def test_the_doctors_tunnel_hint_is_the_same_rule(bound, bind, expected) -> None:
    from personalclaw.dashboard.origin import tunnel_hint

    bound(bind)
    assert tunnel_hint(7777, "box") == f"ssh -L 127.0.0.1:7777:{expected}:7777 box"


def test_the_link_rule() -> None:
    from personalclaw.dashboard.origin import gateway_bind_host, link_host

    assert link_host("0.0.0.0") == "127.0.0.1"
    assert link_host("::") == "[::1]"
    assert link_host("fd00::5") == "[fd00::5]"
    assert link_host("127.0.0.1", "proxy.example") == "proxy.example", "a configured host wins"
    assert gateway_bind_host(True, no_auth=False) == "127.0.0.1"
    assert gateway_bind_host(False, no_auth=False) == "0.0.0.0"
    assert gateway_bind_host(False, no_auth=True) == "127.0.0.1", "no sign-in stays on loopback"


def test_no_printer_spells_a_link_with_a_name() -> None:
    """The command printers build their links through the rule: none spells one with a name."""
    src = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
    for module in ("cli_server.py", "cli_doctor.py", "cli_setup.py"):
        text = (src / module).read_text(encoding="utf-8")
        assert not re.search(r"https?://(personalclaw\.)?localhost[:/{]", text), module
        assert not re.search(r"ssh -L [^\"']*localhost", text), module
