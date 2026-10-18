"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_a_command_reaches_only_its_own_homes_gateway.py``

Its checks:
``tests/rails/test_a_command_reaches_only_its_own_homes_gateway_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast

#: Every module that names the local secret's file, and why none of them sends the secret anywhere
#: but to a gateway that has shown it is this home's.
SECRET_FILE_NAMED_BY = {
    "home_gateway.py": "the one client: it reads the secret for a gateway that showed it is "
    "this home's, and sends it there alone",
    "dashboard/server.py": "the gateway writes it, each time it starts",
    "mcp_core.py": "a child of the gateway (an agent CLI's tool server), addressed through "
    "gateway_base by the gateway that declared its home and port to it, and sending through "
    "home_gateway's loopback transport",
    "inbound/auth.py": "compares an inbound token with it, so that none can equal it",
    "security.py": "names it among PersonalClaw's own secrets, which no agent may read",
    "packs/deny.py": "keeps it out of an exported pack",
    "durability/inventory.py": "lists it as a credential, which a backup and an export leave out",
    "durability/state_history.py": "keeps it out of the home's state history",
    "turn_checkpoints.py": "keeps it out of a turn's checkpoint",
    "workflows/project_export.py": "keeps it out of a project export",
}


def _docstrings(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            if isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                found.add(id(body[0].value))
    return found


def _names_the_secret_file(tree: ast.AST) -> bool:
    docs = _docstrings(tree)
    return any(
        isinstance(node, ast.Constant) and node.value == ".local_secret" and id(node) not in docs
        for node in ast.walk(tree)
    )


def _makes_a_home_gateway(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call)
        and (
            getattr(node.func, "id", "") == "HomeGateway"
            or getattr(node.func, "attr", "") == "HomeGateway"
        )
        for node in ast.walk(tree)
    )


#: The headers that carry this home's secret to its gateway.
_SECRET_HEADERS = {"X-Local-Secret", "X-Internal-Secret"}


def _puts_the_secret_in_a_header(tree: ast.AST) -> bool:
    """Whether *tree* builds a request header that carries the home's secret: a header table with
    one of :data:`_SECRET_HEADERS` as a key, or a call that names the header (``secret_header=``).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and any(
            isinstance(key, ast.Constant) and key.value in _SECRET_HEADERS for key in node.keys
        ):
            return True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value in _SECRET_HEADERS
        ):
            return True
        if isinstance(node, ast.keyword) and node.arg == "secret_header":
            return True
    return False


def _opens_by_itself(tree: ast.AST) -> list[int]:
    """The lines where *tree* sends a request through urllib's own opener (``urlopen``, which
    hands it to a proxy the environment names and follows a redirect), or builds an opener."""
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "attr", None) or getattr(node.func, "id", None))
        in {"urlopen", "build_opener"}
    )
