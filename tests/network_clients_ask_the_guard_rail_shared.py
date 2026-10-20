"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_network_clients_ask_the_guard_rail.py``

Its checks:
``tests/rails/test_network_clients_ask_the_guard_rail_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1] / "src" / "personalclaw"


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


def unguarded_clients(source: str | ast.Module, filename: str = "<source>") -> list[str]:
    """Each client *source* (text, or its tree) opens with no guard in it, as
    ``"<line>: <what>"``."""
    tree = source if isinstance(source, ast.Module) else ast.parse(source, filename=filename)
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
