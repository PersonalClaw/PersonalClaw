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

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
#: The apps that ship inside core.
_BUNDLED_APPS = _CORE / "apps" / "native"

#: Core's provider packages: the model wire clients and every provider kind's built-ins.
_CORE_PROVIDER_PACKAGES = (
    "llm",
    "stt",
    "tts",
    "image_gen",
    "video_gen",
    "embedding_providers",
    "diarization",
    "ocr",
    "search_providers",
    "knowledge_providers",
    "inbox_providers",
    "notification_providers",
    "memory_providers",
    "prompt_providers",
    "sandbox_providers",
    "tool_providers",
    "action_providers",
    "trigger_sources",
    "vector_stores",
    "sync_transports",
)

#: The constructors that put the guard inside the client they return, and the guard a client
#: library's own hook asks.
_GUARDED = frozenset({"http_client", "sync_http_client", "http_session", "RequestGuard"})

#: The guarded constructors whose client an SDK takes as its ``http_client`` (an httpx client).
_HTTPX_CLIENTS = frozenset({"http_client", "sync_http_client"})

#: The calls that make an AWS session, ``(module, name)``: its clients send their own requests and
#: take no HTTP client.
_AWS_SESSIONS = frozenset(
    {
        ("boto3", "Session"),
        ("boto3.session", "Session"),
        ("botocore.session", "Session"),
        ("botocore.session", "get_session"),
    }
)
#: The calls that make a client of boto3's default session, which nobody can hook first.
_AWS_DEFAULT_CLIENTS = frozenset({("boto3", "client"), ("boto3", "resource")})
#: The event an AWS SDK client announces before each request it sends, a retry and a redirect
#: included, for every client of the session it is registered on.
_BEFORE_SEND = "before-send"
_REGISTERS = frozenset({"register", "register_first", "register_last"})
#: What a session's client is made with.
_CLIENT_MAKERS = frozenset({"client", "resource", "create_client"})
#: The guard's own methods, which a hook asks.
_ASKS = frozenset({"ask", "ask_async"})
_SCOPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)

#: ``module -> names`` whose call opens a client or sends a request with no guard in it.
_RAW: dict[str, frozenset[str]] = {
    "httpx": frozenset(
        {
            "AsyncClient",
            "Client",
            "AsyncHTTPTransport",
            "HTTPTransport",
            "get",
            "post",
            "put",
            "patch",
            "delete",
            "head",
            "options",
            "request",
            "stream",
        }
    ),
    "aiohttp": frozenset({"ClientSession", "request"}),
    "requests": frozenset(
        {"get", "post", "put", "patch", "delete", "head", "options", "request", "Session"}
    ),
    "urllib.request": frozenset({"urlopen", "build_opener"}),
    "http.client": frozenset({"HTTPConnection", "HTTPSConnection"}),
    "openai": frozenset({"DefaultHttpxClient", "DefaultAsyncHttpxClient", "DefaultAioHttpClient"}),
    "anthropic": frozenset(
        {"DefaultHttpxClient", "DefaultAsyncHttpxClient", "DefaultAioHttpClient"}
    ),
}

#: The SDK clients that must be handed a guarded ``http_client``, by name: a provider often holds
#: the SDK as a module it was handed (``require_sdk``) rather than one it imported.
_SDK_CLIENTS = frozenset(
    {
        "OpenAI",
        "AsyncOpenAI",
        "AzureOpenAI",
        "AsyncAzureOpenAI",
        "Anthropic",
        "AsyncAnthropic",
        "AnthropicBedrock",
        "AsyncAnthropicBedrock",
        "AnthropicVertex",
        "AsyncAnthropicVertex",
    }
)


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else ""
    return ""


def _named(call: ast.Call) -> str:
    """The last name a call is made by: ``http_client`` for ``net.http_client(...)``."""
    return _dotted(call.func).rpartition(".")[2]


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Each local name in *tree* bound to a module or a module's name: ``hx -> httpx``,
    ``urlopen -> urllib.request.urlopen``."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.partition(".")[0]] = (
                    alias.name if alias.asname else alias.name.partition(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _resolved(call: ast.Call, aliases: dict[str, str]) -> tuple[str, str]:
    """``(module, name)`` a call resolves to through *aliases*, or ``("", "")`` for a call not
    made through an imported name (a method of a local object)."""
    dotted = _dotted(call.func)
    head, _, rest = dotted.partition(".")
    if head not in aliases:
        return "", ""
    full = f"{aliases[head]}.{rest}" if rest else aliases[head]
    module, _, name = full.rpartition(".")
    return module, name


def _is_guarded_value(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Call) and _named(node) in _HTTPX_CLIENTS


class _Scopes:
    """Which function (or the module) each node of a tree belongs to."""

    def __init__(self, tree: ast.AST) -> None:
        self.tree = tree
        self._parent: dict[ast.AST, ast.AST] = {
            child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
        }

    def parent(self, node: ast.AST) -> ast.AST | None:
        return self._parent.get(node)

    def of(self, node: ast.AST) -> ast.AST:
        """The nearest function, class or module *node* is in."""
        at = self._parent.get(node)
        while at is not None and not isinstance(at, _SCOPES):
            at = self._parent.get(at)
        return at if at is not None else self.tree

    def nodes(self, scope: ast.AST) -> list[ast.AST]:
        """Every node whose nearest scope is *scope*, in source order."""
        inside = [n for n in ast.walk(scope) if n is not scope and self.of(n) is scope]
        return sorted(inside, key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))


def _bound_name(call: ast.Call, scopes: _Scopes) -> str:
    """The name *call*'s value is bound to (``session = boto3.Session()``), else ``""``."""
    stmt = scopes.parent(call)
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target = stmt.targets[0]
        return target.id if isinstance(target, ast.Name) else ""
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        return stmt.target.id
    return ""


def _asks_a_guard(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in _ASKS
        for n in ast.walk(node)
    )


def _hook_asks(handler: ast.AST | None, scope: ast.AST, scopes: _Scopes) -> bool:
    """Whether *handler*, registered in *scope*, asks a guard: a lambda that does, or a function
    of that name defined in the scope or the module that does."""
    if isinstance(handler, ast.Lambda):
        return _asks_a_guard(handler.body)
    if not isinstance(handler, ast.Name):
        return False
    for where in (scope, scopes.tree):
        for node in scopes.nodes(where):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == handler.id
            ):
                return _asks_a_guard(node)
    return False


def _registration(call: ast.Call, session: str) -> tuple[str, ast.AST | None] | None:
    """``(event, handler)`` when *call* registers a handler on *session*'s events
    (``session.events.register_first(...)``, or ``session.register(...)`` on a botocore one)."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in _REGISTERS:
        return None
    on = func.value
    if isinstance(on, ast.Attribute) and on.attr == "events":
        on = on.value
    if not (isinstance(on, ast.Name) and on.id == session):
        return None
    given = {kw.arg: kw.value for kw in call.keywords}
    event = call.args[0] if call.args else given.get("event_name")
    handler = call.args[1] if len(call.args) > 1 else given.get("handler")
    name = event.value if isinstance(event, ast.Constant) and isinstance(event.value, str) else ""
    return name, handler


def _unhooked(call: ast.Call, scopes: _Scopes) -> str:
    """What is wrong with the AWS session *call* makes, or ``""`` when every request its clients
    send asks the guard first: it is bound to a name, and in that function a ``before-send``
    handler that asks a guard is registered on it before any client is made from it."""
    session = _bound_name(call, scopes)
    if not session:
        return "an AWS session with no before-send hook that asks the egress guard"
    scope = scopes.of(call)
    hooked_at: tuple[int, int] | None = None
    first_client: tuple[int, int] | None = None
    for node in scopes.nodes(scope):
        if not isinstance(node, ast.Call):
            continue
        at = (node.lineno, node.col_offset)
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _CLIENT_MAKERS
            and isinstance(func.value, ast.Name)
            and func.value.id == session
        ):
            first_client = first_client or at
        registered = _registration(node, session)
        if (
            registered is not None
            and registered[0] == _BEFORE_SEND
            and hooked_at is None
            and _hook_asks(registered[1], scope, scopes)
        ):
            hooked_at = at
    if hooked_at is None:
        return "an AWS session with no before-send hook that asks the egress guard"
    if first_client is not None and first_client < hooked_at:
        return "an AWS session that makes a client before its before-send hook is registered"
    return ""


def unguarded_clients(source: str, filename: str = "<source>") -> list[str]:
    """Each client *source* opens with no guard in it, as ``"<line>: <what>"``."""
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    scopes = _Scopes(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        module, name = _resolved(node, aliases)
        what = f"{module}.{name}" if module else name
        if name in _RAW.get(module, frozenset()):
            found.append(f"{node.lineno}: {what}")
        elif _named(node) in _SDK_CLIENTS:
            given = {kw.arg: kw.value for kw in node.keywords}
            if not _is_guarded_value(given.get("http_client")):
                found.append(f"{node.lineno}: {_dotted(node.func)} with no guarded http_client")
        elif (module, name) in _AWS_SESSIONS:
            if wrong := _unhooked(node, scopes):
                found.append(f"{node.lineno}: {wrong}")
        elif (module, name) in _AWS_DEFAULT_CLIENTS:
            found.append(f"{node.lineno}: a client of boto3's default session ({what})")
    return sorted(found, key=lambda item: int(item.partition(":")[0]))


def _scanned() -> Iterator[tuple[str, Path]]:
    for root in (*(_CORE / package for package in _CORE_PROVIDER_PACKAGES), _BUNDLED_APPS):
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" not in path.parts and not path.name.startswith("test_"):
                yield f"src/personalclaw/{path.relative_to(_CORE)}", path


def test_every_core_provider_package_is_still_where_the_rail_reads():
    missing = [p for p in _CORE_PROVIDER_PACKAGES if not (_CORE / p).is_dir()]
    assert not missing, f"provider packages this rail reads have moved: {missing}"


def test_the_rail_reads_the_provider_clients_it_holds():
    """The vacuity floor: the wire clients the finding was about are read, and each is seen to
    hand its SDK a guarded client, so a scan that read nothing cannot pass."""
    scanned = dict(_scanned())
    for module in (
        "src/personalclaw/llm/openai.py",
        "src/personalclaw/llm/anthropic.py",
        "src/personalclaw/stt/openai_provider.py",
        "src/personalclaw/tts/openai_provider.py",
        "src/personalclaw/image_gen/openai_provider.py",
    ):
        assert module in scanned, f"{module} is not read"
        tree = ast.parse(scanned[module].read_text(encoding="utf-8"))
        guarded = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and _named(node) in _SDK_CLIENTS
            and any(kw.arg == "http_client" and _is_guarded_value(kw.value) for kw in node.keywords)
        ]
        assert guarded, f"{module} builds no SDK client with a guarded http_client"
    ollama = scanned.get("src/personalclaw/apps/native/ollama-models/provider.py")
    assert ollama is not None, "the bundled apps are not read"
    tree = ast.parse(ollama.read_text(encoding="utf-8"))
    made = {_named(n) for n in ast.walk(tree) if isinstance(n, ast.Call)} & _GUARDED
    assert made == {"http_client", "http_session"}, f"the Ollama app's guarded clients: {made}"


def test_no_provider_or_app_opens_a_client_the_guard_is_not_in():
    found = {
        label: unguarded
        for label, path in _scanned()
        if (unguarded := unguarded_clients(path.read_text(encoding="utf-8"), str(path)))
    }
    assert not found, (
        "These open a network client the egress guard is not inside, so a host on Denied hosts "
        "would still be reached through them. Use personalclaw.sdk.net's http_client, "
        "sync_http_client or http_session, or ask its RequestGuard from the library's own hook "
        "before each request, instead:\n"
        + "\n".join(f"  {label}: {', '.join(items)}" for label, items in sorted(found.items()))
    )


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
