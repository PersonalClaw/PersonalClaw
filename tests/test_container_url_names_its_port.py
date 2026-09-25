"""A dashboard URL printed inside a container says whose port it carries — and does not guess.

Measured on the image (2026-09-25): ``docker exec … personalclaw token`` printed
``http://localhost:10000?token=…``, the gateway's port INSIDE the container. A user who published
another host port (``-p 127.0.0.1:19501:10000``) had to know to edit the URL by hand. Nothing in
the container can see the host side of ``-p``, so the fix is not to guess it: both places that
print the URL — the ``token`` command and the startup banner — say the port is the container's
and to use the published one. Outside a container they print exactly what they always did.

Driven through the real printers with only their I/O faked, and no synthetic value here is or was
ever a credential.
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw import cli_server
from personalclaw.dashboard.origin import format_dashboard_urls

_SYNTHETIC_TOKEN = "synthetic.placeholder.not-a-real-token"


class _Response(io.BytesIO):
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _run_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> tuple[str, str]:
    """`personalclaw token` against a faked gateway: its secret file and its token route."""
    (tmp_path / ".local_secret").write_text("synthetic-local-secret\n")
    monkeypatch.setattr(cli_server, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_PORT", "10000")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=5: _Response(json.dumps({"token": _SYNTHETIC_TOKEN}).encode()),
    )
    cli_server._token(argparse.Namespace(ttl="20h", port=None))
    captured = capsys.readouterr()
    return captured.out, captured.err


def test_the_token_command_says_the_port_is_the_containers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """🔑 The URL stays the one line on stdout; the note goes to stderr, naming the port."""
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    out, err = _run_token(monkeypatch, tmp_path, capsys)
    # stdout is still a list of URLs, so `open "$(docker exec … personalclaw token)"` keeps working.
    assert out == f"http://localhost:10000?token={_SYNTHETIC_TOKEN}\n"
    assert "Port 10000 is this container's own port" in err
    assert "docker run -p HOST:10000" in err
    # It must not invent a host port: the only number it may state is the container's own.
    assert [word for word in err.replace("`", " ").split() if word.strip(".,:").isdigit()] == [
        "10000"
    ]


def test_outside_a_container_the_token_command_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """The control arm: a pip or git install prints the URL and nothing else."""
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    out, err = _run_token(monkeypatch, tmp_path, capsys)
    assert out == f"http://localhost:10000?token={_SYNTHETIC_TOKEN}\n"
    assert err == ""


_ORIGIN = "personalclaw.dashboard.origin"


@patch.dict("os.environ", {"PERSONALCLAW_INSTALL_KIND": "container"}, clear=True)
@patch(f"{_ORIGIN}.devspaces_proxy_url", return_value=None)
@patch(f"{_ORIGIN}.machine_hostname", return_value="3f2a9c1d7e4b")
def test_the_startup_banner_says_so_too(_mh: object, _dp: object) -> None:
    """The same family: `docker logs` carries the banner, with the same container port in it."""
    url = f"http://personalclaw.localhost:10000?token={_SYNTHETIC_TOKEN}"
    lines = format_dashboard_urls(url, port=10000, local_only=False)
    assert lines[:2] == ["Dashboard:", f"   {url}"]
    assert any("Port 10000 is this container's own port" in line for line in lines), lines


@patch.dict("os.environ", {}, clear=True)
@patch(f"{_ORIGIN}.devspaces_proxy_url", return_value=None)
@patch(f"{_ORIGIN}.machine_hostname", return_value="localhost")
def test_outside_a_container_the_banner_is_unchanged(_mh: object, _dp: object) -> None:
    lines = format_dashboard_urls("http://localhost:10000?token=t", port=10000)
    assert not any("container" in line for line in lines), lines
