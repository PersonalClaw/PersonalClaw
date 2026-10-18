"""The source-tree checks split from
``tests/test_network_clients_ask_the_guard_rail.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import source_index
from network_clients_ask_the_guard_rail_shared import (
    _CORE,
    _CORE_PROVIDER_PACKAGES,
    _SDK_CLIENTS,
    _is_guarded_value,
    _named,
    unguarded_clients,
)

#: The apps that ship inside core.
_BUNDLED_APPS = _CORE / "apps" / "native"


#: The constructors that put the guard inside the client they return, and the guard a client
#: library's own hook asks.
_GUARDED = frozenset({"http_client", "sync_http_client", "http_session", "RequestGuard"})


def _scanned() -> Iterator[tuple[str, Path]]:
    for root in (*(_CORE / package for package in _CORE_PROVIDER_PACKAGES), _BUNDLED_APPS):
        for path in source_index.under(source_index.package_files(), root):
            if not path.name.startswith("test_"):
                yield f"src/personalclaw/{path.relative_to(_CORE)}", path


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
        tree = source_index.tree(scanned[module])
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
    tree = source_index.tree(ollama)
    made = {_named(n) for n in ast.walk(tree) if isinstance(n, ast.Call)} & _GUARDED
    assert made == {"http_client", "http_session"}, f"the Ollama app's guarded clients: {made}"


def test_no_provider_or_app_opens_a_client_the_guard_is_not_in():
    found = {
        label: unguarded
        for label, path in _scanned()
        if (unguarded := unguarded_clients(source_index.tree(path), str(path)))
    }
    assert not found, (
        "These open a network client the egress guard is not inside, so a host on Denied hosts "
        "would still be reached through them. Use personalclaw.sdk.net's http_client, "
        "sync_http_client or http_session, or ask its RequestGuard from the library's own hook "
        "before each request, instead:\n"
        + "\n".join(f"  {label}: {', '.join(items)}" for label, items in sorted(found.items()))
    )
