"""A tool server whose host cannot be looked up says so, instead of reading as a timeout.

🔴 THE DEFECT (measured before this change, and on the container image). A remote server at a
host no name server answers for read "Not responding — timeout." on its card, while the
gateway's log said ``[Errno -2] Name or service not known``. The container's resolver took about
sixteen seconds to give up on the name, past the probe's fifteen, so the probe reported only that it
had run out of time; and where the lookup failed at once, the card showed the resolver's own words
(``[Errno 8] nodename nor servname provided, or not known``), which name nothing to act on.

The contract now: a connection that failed on its name lookup says that the host could not be
looked up (`mcp_client.unresolved_host_text`), and a probe that ran out of time looks the name up
once more, briefly, and names the lookup when it fails or hangs. An address, or a connection a
proxy makes, is not looked up here, and reads that the server did not answer.

Nothing here touches the network: every lookup is this test's own.
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import patch

import httpx
import pytest

from personalclaw.mcp_client import _failure_text
from personalclaw.mcp_discovery import McpServerInfo, _probe_remote

URL = "http://paperless.invalid/mcp"

#: What the card and an agent's failed call say, as written (`mcp_client.unresolved_host_text`).
UNRESOLVED = (
    "PersonalClaw could not look up paperless.invalid, so it never reached the server. Check the "
    "URL, and that this computer can reach the network paperless.invalid is on."
)
#: And for a lookup the probe gave up on (`mcp_client.slow_lookup_text`).
SLOW_LOOKUP = (
    "Looking up paperless.invalid did not finish within 0.2 seconds, so PersonalClaw never "
    "reached the server. Check the URL, and that this computer can reach the network "
    "paperless.invalid is on."
)


def _connect_error(errno: int, words: str) -> BaseException:
    """What the SDK's transport raises for a host that does not resolve: httpx's connect error,
    raised from the resolver's ``gaierror``, inside the task group's exception group."""
    try:
        try:
            raise socket.gaierror(errno, words)
        except socket.gaierror as cause:
            raise httpx.ConnectError(f"[Errno {errno}] {words}") from cause
    except httpx.ConnectError as exc:
        return BaseExceptionGroup("unhandled errors in a TaskGroup", [exc])


@pytest.mark.parametrize(
    ("errno", "words"),
    [(-2, "Name or service not known"), (-3, "Temporary failure in name resolution")],
)
def test_a_connection_that_failed_on_its_name_says_the_host_could_not_be_looked_up(
    errno, words
) -> None:
    said = _failure_text(_connect_error(errno, words), URL, "paperless")
    assert said == UNRESOLVED, said
    assert "Errno" not in said and words not in said


class _Probe:
    """``_probe_remote`` of a server that never answers, with this test's own name lookup."""

    def __init__(self, url: str, look_up) -> None:
        self.server = McpServerInfo(name="paperless", url=url, transport="http")
        self.look_up = look_up
        self.looked_up: list[str] = []

    async def run(self) -> McpServerInfo:
        async def never_answers(_conn, _stack):
            # The connection is opened and nothing ever answers: the probe's own deadline ends it.
            await asyncio.sleep(30)

        async def look_up(host: str) -> None:
            self.looked_up.append(host)
            await self.look_up(host)

        # `create=True`: without the fix there is no lookup to replace, and the test fails on what
        # the probe says rather than on the patch.
        with (
            patch("personalclaw.mcp_discovery._get_probe_timeout", return_value=0.2),
            patch("personalclaw.mcp_discovery._look_up", look_up, create=True),
            patch("personalclaw.mcp_discovery._proxied", return_value=False, create=True),
            patch("personalclaw.mcp_client.McpServerConn._open_transport", never_answers),
        ):
            return await _probe_remote(self.server)


async def _does_not_resolve(host: str) -> None:
    raise socket.gaierror(-2, "Name or service not known")


async def _hangs(host: str) -> None:
    await asyncio.sleep(30)


async def _resolves(host: str) -> None:
    return None


@pytest.mark.asyncio
async def test_a_probe_that_ran_out_of_time_on_a_name_that_does_not_resolve_names_the_lookup():
    probe = _Probe(URL, _does_not_resolve)
    result = await probe.run()
    assert result.status == "error"
    assert result.error == UNRESOLVED, result.error
    assert probe.looked_up == ["paperless.invalid"]


@pytest.mark.asyncio
async def test_a_lookup_that_hangs_is_named_as_what_the_probe_waited_for():
    result = await _Probe(URL, _hangs).run()
    assert result.error == SLOW_LOOKUP, result.error


@pytest.mark.asyncio
async def test_a_host_whose_name_resolves_did_not_answer():
    result = await _Probe(URL, _resolves).run()
    assert result.error == "paperless.invalid did not answer within 0.2 seconds."


@pytest.mark.asyncio
async def test_an_address_is_never_looked_up():
    probe = _Probe("http://127.0.0.1:9/mcp", _does_not_resolve)
    result = await probe.run()
    assert result.error == "127.0.0.1 did not answer within 0.2 seconds."
    assert probe.looked_up == []


def test_a_connection_a_proxy_makes_is_not_this_computers_lookup(monkeypatch) -> None:
    from personalclaw.mcp_discovery import _proxied

    for var in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.test:3128")
    monkeypatch.setenv("http_proxy", "http://proxy.example.test:3128")
    assert _proxied(URL, "paperless.invalid")
    monkeypatch.setenv("NO_PROXY", "paperless.invalid")
    monkeypatch.setenv("no_proxy", "paperless.invalid")
    assert not _proxied(URL, "paperless.invalid")
