"""The source-tree checks split from
``tests/test_a_command_reaches_only_its_own_homes_gateway.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import source_index
from a_command_reaches_only_its_own_homes_gateway_shared import (
    SECRET_FILE_NAMED_BY,
    _makes_a_home_gateway,
    _names_the_secret_file,
    _opens_by_itself,
    _puts_the_secret_in_a_header,
)

import personalclaw
from personalclaw import cli_server

SRC = Path(personalclaw.__file__).resolve().parent


HARNESS = SRC.parents[1] / "harness"


def _trees(root: Path) -> Iterator[tuple[str, ast.Module]]:
    """Every ``.py`` file under *root* with its tree. The package is the suite's one source
    index; any other root (the dev tools under ``harness/``) is walked and parsed here."""
    if root == SRC:
        for path, tree in source_index.package_trees():
            yield path.relative_to(root).as_posix(), tree
        return
    for path in sorted(root.rglob("*.py")):
        yield path.relative_to(root).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def test_only_the_one_client_reads_the_secret_to_send_it():
    """A module that reads ``.local_secret`` can send it anywhere; the client that sends it is
    ``home_gateway``, and every other module that names the file says why it sends it nowhere."""
    found = {rel for rel, tree in _trees(SRC) if _names_the_secret_file(tree)}
    assert found == set(SECRET_FILE_NAMED_BY), (
        "a module names the local secret's file without being one the rail knows: send the secret "
        "through home_gateway.reach(), or say here why the module never sends it: "
        f"{sorted(found ^ set(SECRET_FILE_NAMED_BY))}"
    )
    tools = [rel for rel, tree in _trees(HARNESS) if _names_the_secret_file(tree)]
    assert tools == [], f"a dev tool reads the secret itself: {tools}"


def test_only_reach_makes_a_home_gateway():
    """A ``HomeGateway`` is the proof a gateway showed it is this home's: made anywhere but
    ``reach``, it would be a port taken on trust with the secret's door open."""
    makers = [rel for rel, tree in _trees(SRC) if _makes_a_home_gateway(tree)]
    assert makers == ["home_gateway.py"], makers
    tree = source_index.tree(SRC / "home_gateway.py")
    functions = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and _makes_a_home_gateway(node)
    ]
    assert functions == ["reach"], functions
    assert [rel for rel, tree in _trees(HARNESS) if _makes_a_home_gateway(tree)] == []


def test_every_request_that_carries_the_secret_goes_through_the_one_loopback_transport():
    """A module that puts the home's secret in a request header sends the request through
    ``home_gateway.open_loopback``: no proxy from the environment, no redirect followed. The
    senders are found in the tree, so a new one is held to this as it is written."""
    trees = {rel: tree for rel, tree in _trees(SRC)}
    trees.update({f"harness/{rel}": tree for rel, tree in _trees(HARNESS)})
    senders = {rel for rel, tree in trees.items() if _puts_the_secret_in_a_header(tree)}
    known = {
        "home_gateway.py",
        "cli_server.py",
        "auth/cli.py",
        "schedule_trigger.py",
        "mcp_core.py",
        "mcp_shared.py",
    }
    assert known <= senders, f"the census no longer sees {sorted(known - senders)}"
    own = {
        rel: lines
        for rel in sorted(senders - {"home_gateway.py"})
        if (lines := _opens_by_itself(trees[rel]))
    }
    assert own == {}, (
        "these send the home's secret through an opener of their own: send it through "
        f"home_gateway.open_loopback(), or through home_gateway.reach(): {own}"
    )


#: The modules a command that talks to its gateway runs in.
CLIENTS = (
    "cli.py",
    "cli_server.py",
    "cli_run.py",
    "cli_chat.py",
    "cli_commands.py",
    "cli_doctor.py",
    "auth/cli.py",
    "schedule_trigger.py",
    "home_gateway.py",
)


#: What answers where a gateway BINDS, or where a CHILD of one connects: never where a command
#: connects, which is home_gateway.reach()'s answer.
_NOT_WHERE_A_COMMAND_CONNECTS = {
    "_DEFAULT_PORT",
    "DASHBOARD_PORT",
    "resolve_api_base",
    "resolve_port",
    "live_port",
    "_api_base",
    "_post",
    "_get",
    "_delete",
}


def test_no_command_asks_the_old_resolvers_for_a_port():
    """``cli_server.resolve_client_port`` is gone, and no client module reads the port a gateway
    binds, or the address a child of the gateway is handed, as where a command connects."""
    assert not hasattr(cli_server, "resolve_client_port")
    for rel in CLIENTS:
        tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = {getattr(node, "id", None), getattr(node, "attr", None)}
            if isinstance(node, ast.ImportFrom):
                names |= {alias.name for alias in node.names}
            hit = names & _NOT_WHERE_A_COMMAND_CONNECTS
            assert not hit, (
                f"{rel}:{getattr(node, 'lineno', '?')} reads {sorted(hit)}: a command connects "
                "where home_gateway.reach() says"
            )
