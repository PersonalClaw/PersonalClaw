"""The source-tree checks split from
``tests/test_triggers_web_poll.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import triggers_web_poll_shared

from personalclaw.triggers import web_poll

_isolate_knowledge = triggers_web_poll_shared._isolate_knowledge


def test_the_DEFAULT_fetcher_is_the_EGRESS_CHOKEPOINT():
    """🔴 A watch pointed at `http://169.254.169.254/` is an SSRF against the machine's own metadata
    service. `net.fetch` is where host classification, private-IP denial, redirect-hop re-checks,
    the byte cap and the timeout already live — a direct fetch here would bypass all of it.

    Asserted on the source because the property is WHICH layer is called, and a behavioural test
    would need a network."""
    import ast
    import inspect

    src = inspect.getsource(web_poll._fetch)
    assert "from personalclaw.net import fetch" in src

    # Parsed rather than grepped: the docstring NAMES `urllib`/`httpx` as the layers this must not
    # use, so a substring check trips on its own prose. The property is which modules the code
    # imports, which is a question about the AST.
    tree = ast.parse(inspect.getsource(web_poll))
    imported = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not (
        imported & {"urllib", "httpx", "requests", "aiohttp", "socket"}
    ), f"web_poll must fetch ONLY through the egress chokepoint; it imports {sorted(imported)}"
