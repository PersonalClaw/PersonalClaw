"""Every keychain call names the home's own namespace, through the one function that makes it.

The OS keychain is the machine's, and every PersonalClaw home on the machine reaches the same one.
What keeps one home out of another's items is the service name each call passes: the home's own,
from ``config.credentials.keychain_service``. A call that passes anything else — the default home's
name spelled out, a constant made at import, a parameter, a name that falls back to a literal —
files under a name the home does not own, and that is how every home came to read, overwrite and
delete the default home's secrets. So this reads the source, not one call site's memory of the rule:

* **a keychain call** is a call to one of keyring's item verbs (``get_password``,
  ``set_password``, ``delete_password``, ``get_credential``) in a module that reaches a keychain:
  one that imports ``keyring`` (statically or by name), or calls a DOOR — a function that imports
  ``keyring`` and returns it, found by reading the tree, so a new door is covered the day it lands;
* **it names the home's namespace** when its service argument is a call to ``keychain_service``,
  or a local name every binding of which, in the function the call is in, is such a call.

A module that never reaches a keychain (the gateway's own login, whose ``set_password`` is its
password) is not read as one.
"""

from __future__ import annotations

import ast
import functools
from dataclasses import dataclass
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: keyring's verbs that read, write or delete an item: each takes the service name first.
VERBS = frozenset({"get_password", "set_password", "delete_password", "get_credential"})

#: The one function that makes a home's keychain service name.
SCOPER = "keychain_service"

#: The module that defines it, and the only one that may.
SCOPER_MODULE = "config/credentials.py"


@dataclass(frozen=True)
class KeychainCall:
    line: int
    text: str
    names_the_home: bool


def _callee(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_scoper_call(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Call) and _callee(node.func) == SCOPER


def _keyring_module(name: str) -> bool:
    return name == "keyring" or name.startswith("keyring.")


def _imports_keyring(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(_keyring_module(a.name) for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and _keyring_module(node.module or ""):
            return True
        if (
            isinstance(node, ast.Call)
            and _callee(node.func) in ("import_module", "__import__")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and _keyring_module(str(node.args[0].value))
        ):
            return True
    return False


def doors(tree: ast.AST) -> set[str]:
    """Functions in *tree* that hand out the keyring module: they import it and return it."""
    found: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bound = {
            alias.asname or alias.name
            for node in ast.walk(fn)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == "keyring"
        }
        if any(
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Name)
            and node.value.id in bound
            for node in ast.walk(fn)
        ):
            found.add(fn.name)
    return found


def _reaches_a_keychain(tree: ast.AST, known_doors: set[str]) -> bool:
    if _imports_keyring(tree):
        return True
    return any(
        isinstance(node, ast.Call) and _callee(node.func) in known_doors for node in ast.walk(tree)
    )


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST | None:
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return node
    return None


def _bound_only_to_the_scoper(name: str, fn: ast.AST | None, parents: dict) -> bool:
    """Whether every binding of *name* in *fn* is ``name = keychain_service(...)``. A name bound
    outside any function is a constant made at import, which freezes whichever home was active."""
    if fn is None:
        return False
    args = fn.args  # type: ignore[attr-defined]
    params = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
    if any(p is not None and p.arg == name for p in params):
        return False
    stores = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store)
    ]
    if not stores:
        return False
    for store in stores:
        parent = parents.get(store)
        if not (
            isinstance(parent, ast.Assign)
            and len(parent.targets) == 1
            and parent.targets[0] is store
            and _is_scoper_call(parent.value)
        ):
            return False
    return True


def _service_argument(call: ast.Call) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg in ("service_name", "service"):
            return keyword.value
    return call.args[0] if call.args else None


def keychain_calls(source: str, known_doors: set[str] | None = None) -> list[KeychainCall]:
    """Every keychain call in *source*, and whether it names the home's own namespace."""
    return _keychain_calls_in(ast.parse(source), known_doors or set())


def _keychain_calls_in(tree: ast.Module, known_doors: set[str]) -> list[KeychainCall]:
    door_names = known_doors | doors(tree)
    if not _reaches_a_keychain(tree, door_names):
        return []
    bare = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and _keyring_module(node.module or "")
        for alias in node.names
        if alias.name in VERBS
    }
    parents = _parents(tree)
    found: list[KeychainCall] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            (isinstance(func, ast.Attribute) and func.attr in VERBS)
            or (isinstance(func, ast.Name) and func.id in bare)
        ):
            continue
        service = _service_argument(node)
        names_the_home = _is_scoper_call(service) or (
            isinstance(service, ast.Name)
            and _bound_only_to_the_scoper(service.id, _enclosing_function(node, parents), parents)
        )
        found.append(KeychainCall(node.lineno, ast.unparse(node)[:160], names_the_home))
    return found


@functools.cache
def _modules() -> dict[str, ast.Module]:
    return {
        str(path.relative_to(SRC)): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(SRC.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


@functools.cache
def _tree_doors() -> frozenset[str]:
    return frozenset(name for tree in _modules().values() for name in doors(tree))


@functools.cache
def _census() -> dict[str, list[KeychainCall]]:
    known = set(_tree_doors())
    out: dict[str, list[KeychainCall]] = {}
    for rel, tree in _modules().items():
        calls = _keychain_calls_in(tree, known)
        if calls:
            out[rel] = calls
    return out


# ── the tree ─────────────────────────────────────────────────────────────────


def test_every_keychain_call_names_the_homes_own_namespace():
    wrong = [
        f"{rel}:{call.line}: {call.text}"
        for rel, calls in _census().items()
        for call in calls
        if not call.names_the_home
    ]
    assert wrong == [], (
        f"pass {SCOPER}() as the service of every keychain call — any other name files a home's "
        "items where another home reads them:\n  " + "\n  ".join(wrong)
    )


def test_the_census_reads_the_real_keychain_calls():
    """Vacuity: the census finds the store's reads, writes, deletes and index entries — a census
    that found no call would pass the test above whatever the tree held."""
    census = _census()
    assert set(census) == {SCOPER_MODULE}, census
    verbs = {call.text.split("(", 1)[0].rsplit(".", 1)[-1] for call in census[SCOPER_MODULE]}
    assert verbs == {"get_password", "set_password", "delete_password"}, verbs
    assert len(census[SCOPER_MODULE]) >= 6


def test_the_namespace_is_made_in_one_place():
    """One function makes the name. A second ``keychain_service`` would pass the census with a name
    of its own making."""
    defined = [
        rel
        for rel, tree in _modules().items()
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == SCOPER
    ]
    assert defined == [SCOPER_MODULE]


def test_the_tree_has_a_door_and_the_census_sees_it():
    assert _tree_doors(), "no function hands out the keyring module, so nothing reaches it"


# ── planted calls ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "planted",
    [
        "import keyring\n\ndef read(key):\n    return keyring.get_password('personalclaw', key)\n",
        "import keyring\n\ndef write(key, value):\n    keyring.set_password(SERVICE, key, value)\n",
        "from keyring import delete_password\n\ndef drop(key):\n"
        "    delete_password(_DEFAULT_HOME_SERVICE, key)\n",
        "import keyring\n\ndef read(key):\n"
        "    return keyring.get_password(service_name='personalclaw', username=key)\n",
        # A name that falls back to the default home's name is the leak itself.
        "import keyring\n\ndef read(key):\n    service = keychain_service()\n"
        "    if not service:\n        service = 'personalclaw'\n"
        "    return keyring.get_password(service, key)\n",
        # A constant made at import freezes whichever home was active then.
        "import keyring\n\nSERVICE = keychain_service()\n\ndef read(key):\n"
        "    return keyring.get_password(SERVICE, key)\n",
        "import keyring\n\ndef read(service, key):\n"
        "    return keyring.get_password(service, key)\n",
        "import importlib\n\ndef read(key):\n"
        "    return importlib.import_module('keyring').get_password('personalclaw', key)\n",
    ],
    ids=[
        "the-default-homes-name-spelled-out",
        "a-constant",
        "a-bare-verb-imported-from-keyring",
        "the-service-by-keyword",
        "a-local-that-falls-back-to-a-literal",
        "a-name-made-at-import",
        "a-parameter",
        "keyring-imported-by-name",
    ],
)
def test_the_census_fails_a_call_that_names_a_service_of_its_own(planted):
    calls = keychain_calls(planted)
    assert len(calls) == 1 and not calls[0].names_the_home, calls


def test_the_census_fails_a_call_through_the_stores_own_door():
    """The planted module reaches the keychain the way the store does, through its door."""
    door = sorted(_tree_doors())[0]
    planted = (
        f"def read(key):\n    kr = {door}()\n    return kr.get_password('personalclaw', key)\n"
    )
    calls = keychain_calls(planted, set(_tree_doors()))
    assert len(calls) == 1 and not calls[0].names_the_home, calls


@pytest.mark.parametrize(
    "compliant",
    [
        "import keyring\n\ndef read(key):\n"
        "    return keyring.get_password(keychain_service(), key)\n",
        "import keyring\n\ndef write(key, value):\n"
        "    service = keychain_service(mint=True)\n"
        "    if not service:\n        return False\n"
        "    keyring.set_password(service, key, value)\n",
        "import keyring\nfrom personalclaw.config import credentials\n\ndef read(key, home):\n"
        "    return keyring.get_password(credentials.keychain_service(home), key)\n",
    ],
    ids=["the-call-itself", "a-local-bound-only-to-it", "through-the-module"],
)
def test_the_census_passes_a_call_that_names_the_homes_own_namespace(compliant):
    calls = keychain_calls(compliant)
    assert len(calls) == 1 and calls[0].names_the_home, calls


def test_a_module_that_never_reaches_a_keychain_is_not_read_as_one():
    """The gateway's own login has a ``set_password`` too, and it is the owner's password."""
    source = (
        "from personalclaw.auth import credentials as creds\n\n"
        "def change(user, plaintext):\n    creds.set_password(user, plaintext)\n"
    )
    assert keychain_calls(source, set(_tree_doors())) == []
