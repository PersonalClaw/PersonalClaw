"""Rail: a model provider or an app opens no HTTP client that the egress guard is not inside.

A provider's chat went to a host on Denied hosts because the vendor SDK sent it with an HTTP client
of its own, which asked nothing, while the provider's Test went through the guard. Every client a
provider or an app sends requests with now comes from the guarded constructors
(``personalclaw.sdk.net``: ``http_client``, ``sync_http_client`` and ``http_session``), which ask
the guard about every request, each redirect hop included. A client library that takes no HTTP
client asks the guard itself (``RequestGuard``) from its own hook: an AWS session's clients from a
``before-send`` handler registered on the session, which the AWS SDK runs before every request any
client of the session sends. That glue is the library's, so it lives in the app that uses it, and
this rail recognises it by its shape, not by a name. This reads the source and fails on any other
way of opening a client:

* an ``httpx``, ``aiohttp``, ``requests``, ``urllib.request`` or ``http.client`` client or request;
* an ``openai`` or ``anthropic`` SDK client not handed a guarded ``http_client``, or the SDK's own
  default HTTP client;
* an AWS session (``boto3.Session``, ``botocore.session.get_session``) bound to a name in a
  function with no ``before-send`` handler registered on it there, before any client is made from
  it, whose body asks a guard (``.ask(...)``), and a client of boto3's default session
  (``boto3.client``, ``boto3.resource``), which nobody can hook first.

What it reads: every module of core's provider packages, and every app bundled in core. The
first-party apps repository holds its own apps to the same rule in its own CI
(``.github/scripts/check_network_clients.py`` there), so neither repository's verdict depends on
the other's working tree. Connections that are not HTTP (a WebSocket, a mail server's IMAP or SMTP,
a raw socket) are outside its vocabulary.
"""

from __future__ import annotations

import pytest
from network_clients_ask_the_guard_rail_shared import (
    _CORE,
    _CORE_PROVIDER_PACKAGES,
    unguarded_clients,
)


def test_every_core_provider_package_is_still_where_the_rail_reads():
    missing = [p for p in _CORE_PROVIDER_PACKAGES if not (_CORE / p).is_dir()]
    assert not missing, f"provider packages this rail reads have moved: {missing}"


# ── the rail itself ───────────────────────────────────────────────────────────


#: An AWS session whose clients ask the guard: a ``before-send`` handler that asks it, registered
#: on the session before any client is made from it.
_HOOKED_SESSION = """\
import boto3
from personalclaw.sdk.net import RequestGuard


def session_for(profile):
    session = boto3.Session(profile_name=profile)
    guard = RequestGuard(model_provider=True)

    def ask(request, **_event):
        guard.ask(str(request.url))

    session.events.register_first("before-send", ask)
    return session
"""


@pytest.mark.parametrize(
    "source",
    [
        "import httpx\nclient = httpx.AsyncClient(base_url='https://api.example.com')\n",
        "from httpx import Client\nClient()\n",
        "import aiohttp\n"
        "async def f():\n    async with aiohttp.ClientSession() as s:\n        pass\n",
        "from aiohttp import ClientSession as S\nS()\n",
        "import urllib.request\nurllib.request.urlopen('https://example.com')\n",
        "from urllib.request import urlopen\nurlopen('https://example.com')\n",
        "import requests\nrequests.get('https://example.com')\n",
        "import openai\nopenai.AsyncOpenAI(api_key='k')\n",
        "import openai\n"
        "openai.AsyncOpenAI(api_key='k', http_client=openai.DefaultAsyncHttpxClient())\n",
        "import openai\nfrom personalclaw.sdk.net import RequestGuard\n"
        "openai.AsyncOpenAI(api_key='k', http_client=RequestGuard())\n",
        "import anthropic\nanthropic.AsyncAnthropic(api_key='k')\n",
        "import boto3\nboto3.client('bedrock-runtime')\n",
        "import boto3\nsession = boto3.Session()\nsession.client('s3', region_name='us-east-1')\n",
        "from boto3 import Session\nSession(profile_name='p').client('bedrock')\n",
        "import botocore.session\nbotocore.session.get_session().create_client('s3')\n",
        # A session made somewhere else than a name, with no hook to register.
        _HOOKED_SESSION.replace("session = boto3.Session(profile_name=profile)", "session = None")
        + "boto3.Session().client('s3')\n",
        # The hook registered after a client was made: that client asks nothing.
        _HOOKED_SESSION.replace(
            "    session.events.register_first(",
            "    session.client('s3')\n    session.events.register_first(",
        ),
        # A hook on another event, or one that asks no guard.
        _HOOKED_SESSION.replace('"before-send"', '"after-call"'),
        _HOOKED_SESSION.replace("guard.ask(str(request.url))", "print(request.url)"),
        # A hook on one operation only: every other request asks nothing.
        _HOOKED_SESSION.replace('"before-send"', '"before-send.s3.ListBuckets"'),
    ],
)
def test_the_rail_catches_a_client_with_no_guard_in_it(source: str):
    assert unguarded_clients(source), f"the rail let this through:\n{source}"


@pytest.mark.parametrize(
    "source",
    [
        "from personalclaw.sdk.net import http_client\n"
        "http_client(base_url='https://x.example')\n",
        "from personalclaw.sdk import net\nnet.http_session(model_provider=True)\n",
        "import openai\nfrom personalclaw.sdk.net import http_client\n"
        "openai.AsyncOpenAI(api_key='k', http_client=http_client(model_provider=True))\n",
        _HOOKED_SESSION,
        # A lambda that asks, registered with ``register``.
        _HOOKED_SESSION.replace(
            'session.events.register_first("before-send", ask)',
            'session.events.register("before-send", lambda request, **_: guard.ask(request.url))',
        ),
        "import json\njson.loads('{}')\n",
    ],
)
def test_the_rail_lets_a_guarded_client_through(source: str):
    assert unguarded_clients(source) == []


def test_a_hooked_session_is_one_the_rail_reads_as_hooked():
    """The seeded hooked session is read as hooked, and the same session with its registration
    taken out is not, so the pass above is the hook's, not a reader that sees no session."""
    assert unguarded_clients(_HOOKED_SESSION) == []
    unhooked = _HOOKED_SESSION.replace(
        '    session.events.register_first("before-send", ask)\n', ""
    )
    assert unguarded_clients(unhooked) == [
        "6: an AWS session with no before-send hook that asks the egress guard"
    ]
