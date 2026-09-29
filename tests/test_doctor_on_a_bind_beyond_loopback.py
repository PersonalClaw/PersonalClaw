"""``personalclaw doctor`` on a gateway bound beyond loopback — the documented container.

The one-container ``docker run`` sets ``PERSONALCLAW_BIND_HOST=0.0.0.0``, because a container's
loopback cannot be published, and leaves ``dashboard.url`` empty. Doctor run inside it exited 1:

    auth:        ✅ token auth required (via !dashboard)
    auth:        ⚠️  no channel configured — token generation unavailable
    ...
    ❌ Fix these issues: dashboard auth: remote bind without a channel, cannot verify dashboard
    auth (host unknown)

Neither issue was one. ``personalclaw token`` mints a sign-in link from the running gateway with
no channel at all (``docker exec … personalclaw token`` is the README's second line), and
"host unknown" failed the one check that matters on such a bind — that a request with no token is
refused on an interface other than loopback — without trying it, because it only knew the host
``dashboard.url`` names. The machine's own address answers that question just as well.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from email.message import Message
from unittest.mock import patch

import pytest

_PORT = 10000
_OWN_ADDRESS = "10.4.0.12"


class _Answer:
    """A 200 from ``/api/status`` — a request the gateway served."""

    def __init__(self, body: bytes = b'{"uptime": "1m"}') -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Answer":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _refused(url: str) -> urllib.error.HTTPError:
    """The token gate's refusal of a request that carries no token."""
    return urllib.error.HTTPError(url, 403, "Forbidden", hdrs=Message(), fp=None)


def _run_doctor(
    capsys,
    monkeypatch,
    *,
    addresses: list[str],
    tokenless_answer_on: frozenset[str] = frozenset(),
    env: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Run ``_doctor()`` on an all-interfaces bind with no channel; return (output, URLs asked).

    Every ``/api/status`` request is refused with a 403 — the gateway's token gate — except those
    to a host in *tokenless_answer_on*, which it serves.
    """
    from personalclaw.cli_doctor import _doctor

    monkeypatch.setenv("PERSONALCLAW_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("PERSONALCLAW_PORT", str(_PORT))
    for name in (
        "PERSONALCLAW_BYPASS_LOCAL_NETWORKS",
        "PERSONALCLAW_DEV_NO_AUTH",
        "SSH_CONNECTION",
        "SSH_CLIENT",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)

    asked: list[str] = []

    def urlopen(req, timeout=None):  # noqa: ANN001 — urllib's own signature
        url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        asked.append(url)
        host = url.split("://", 1)[1].split(":", 1)[0]
        if host in tokenless_answer_on:
            return _Answer()
        raise _refused(url)

    with (
        patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
        # This machine's addresses are the test's: no route answer, and these for the hostname.
        patch("personalclaw.dashboard.origin._routed_address", return_value=""),
        patch("personalclaw.dashboard.origin._local_addresses", return_value=list(addresses)),
        patch("urllib.request.urlopen", side_effect=urlopen),
    ):
        try:
            _doctor()
        except SystemExit:
            pass
    return capsys.readouterr().out, asked


def _issues(out: str) -> str:
    """The summary line's issue list, or '' when doctor found none."""
    for line in out.splitlines():
        if line.startswith("❌ Fix these issues:"):
            return line
    return ""


def test_no_channel_is_not_a_fault_and_the_sign_in_command_is_named(capsys, monkeypatch):
    out, _ = _run_doctor(capsys, monkeypatch, addresses=["127.0.0.1", _OWN_ADDRESS])
    assert "bind:        0.0.0.0" in out, "the probe did not reach the all-interfaces branch"
    assert "token generation unavailable" not in out, out
    assert "!dashboard" not in out, "a command one channel app has is not how everyone signs in"
    assert "auth:        🔒 token required on every interface (run: personalclaw token" in out, out
    issues = _issues(out)
    assert "channel" not in issues, issues
    assert "host unknown" not in issues, issues


def test_the_external_check_asks_this_machines_own_address_when_no_host_is_configured(
    capsys, monkeypatch
):
    out, asked = _run_doctor(capsys, monkeypatch, addresses=["127.0.0.1", _OWN_ADDRESS])
    assert f"http://{_OWN_ADDRESS}:{_PORT}/api/status" in asked, asked
    assert f"auth check:  ✅ a request with no token is refused at {_OWN_ADDRESS}" in out, out
    assert "dashboard auth" not in _issues(out), _issues(out)


def test_a_tokenless_answer_at_this_machines_own_address_is_a_fault(capsys, monkeypatch):
    """The check is real: served without a token beyond loopback is reported, and fails doctor."""
    out, _ = _run_doctor(
        capsys,
        monkeypatch,
        addresses=["127.0.0.1", _OWN_ADDRESS],
        tokenless_answer_on=frozenset({_OWN_ADDRESS}),
    )
    assert f"auth check:  ❌ {_OWN_ADDRESS} answered a request with no token" in out, out
    assert "dashboard auth: no token required on external interface" in _issues(out), out


def test_with_no_address_beyond_loopback_it_says_the_check_did_not_run(capsys, monkeypatch):
    out, asked = _run_doctor(capsys, monkeypatch, addresses=["127.0.0.1", "::1"])
    assert "auth check:  ⏭  not checked" in out, out
    assert not [u for u in asked if "127.0.0.1" not in u], asked
    assert "host unknown" not in _issues(out), _issues(out)


@pytest.mark.parametrize(
    ("env", "claim"),
    [
        (
            {"PERSONALCLAW_BYPASS_LOCAL_NETWORKS": "1"},
            "auth:        ⚠️  no token needed from a private-network address"
            " (PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1); a token everywhere else",
        ),
        (
            {"PERSONALCLAW_DEV_NO_AUTH": "1"},
            "auth:        ❌ off — every request is served without a token",
        ),
    ],
    ids=["local-network-bypass", "auth-off"],
)
def test_the_auth_row_says_what_the_gateway_does_beyond_loopback(capsys, monkeypatch, env, claim):
    """The row mirrors the token gate, as the loopback branch does, instead of claiming a token."""
    out, _ = _run_doctor(capsys, monkeypatch, addresses=["127.0.0.1", _OWN_ADDRESS], env=env)
    assert claim in out, out
    assert "token required on every interface" not in out, out


def test_a_bind_beyond_loopback_is_not_called_local_only(capsys, monkeypatch):
    """Doctor cannot see what reaches such a bind — this machine's interfaces, or the port a
    container published — so it says what it established: there is no tailnet address."""
    monkeypatch.setattr("personalclaw.cli_doctor.tailnet_ip", lambda: "")
    out, _ = _run_doctor(capsys, monkeypatch, addresses=["127.0.0.1", _OWN_ADDRESS])
    assert "remote:      local-only" not in out, out
    assert "remote:      no tailnet address found" in out, out


# ── where "this machine's own address" comes from ─────────────────────────────────────────────


def test_the_kernels_route_answers_first_then_the_hostname(monkeypatch):
    """A Mac's hostname often resolves to nothing, so the route is asked first; a container's
    hostname resolves to its own address, which covers a host with no route to the link."""
    from personalclaw.dashboard import origin

    monkeypatch.setattr(origin, "_local_addresses", lambda: ["127.0.0.1", "10.4.0.12"])
    monkeypatch.setattr(origin, "_routed_address", lambda: "192.168.1.20")
    assert origin.address_beyond_loopback() == "192.168.1.20"
    monkeypatch.setattr(origin, "_routed_address", lambda: "")
    assert origin.address_beyond_loopback() == "10.4.0.12"


@pytest.mark.parametrize(
    "addresses",
    [["127.0.0.1"], ["::1", "fe80::1%en0"], ["0.0.0.0"], ["not-an-address"], []],
)
def test_nothing_beyond_loopback_names_no_address(addresses):
    from personalclaw.dashboard.origin import address_beyond_loopback

    assert address_beyond_loopback(addresses) == ""
