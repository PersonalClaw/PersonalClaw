"""Every session key the source mints names a kind ``session_keys`` classifies, read from its row.

Whether anybody watches a piece of work is decided by the kind of session key it runs under
(``personalclaw.session_keys``): the safety profile, the approval posture of a run nobody can be
asked in, the spend caps, the self-stop check and the autonomy ladder all read that answer. A key
whose kind has no row there matches nothing, and nothing reads as a chat someone is watching. So
a new kind of key minted without a row is new unattended work judged as watched, and a prefix
spelled at a mint site instead of read from its row is a kind that can drift from its own
classification.

This census reads the source tree for the places a string becomes a session key and fails on:

* a key minted under a prefix no row names, and
* a row's prefix spelled where a key is minted, instead of read from the row
  (``session_keys.TILE.key(…)``, ``session_keys.TILE.prefix``).

It follows a key built from a name to what the name holds: a variable of the function it is in,
a constant of its module, or a constant imported from another. A record of who did something (an
audit row, a usage row) labels the row and is judged by nothing, so its key is not a mint.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from personalclaw import session_keys
from personalclaw.guardrails.policy import is_unattended_session

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
PACKAGE = SRC / "personalclaw"
REGISTRY = "personalclaw.session_keys"

#: Keyword arguments that hand a key to a session, a gate or a reader of the classification.
KEY_KEYWORDS = frozenset({"session_key", "parent_session_key", "linked_session_key"})

#: Calls that take a key positionally: the callee's name and the argument's index. Each opens
#: a session under the key, runs work as its work, or asks whether anybody watches it.
KEY_POSITIONAL = {
    "get_or_create": 0,
    "get_or_create_session": 0,
    "destroy": 0,
    "set_current_session_key": 0,
    "as_work_of": 0,
    "work_context": 0,
    "hand_on": 1,
    "is_unattended_session": 0,
    "profile_for_session": 0,
    "approval_policy_for_session": 0,
    "egress_held_to": 0,
    "egress_policy_for_run": 0,
    "is_unattended": 0,
}

#: A keyword a call above also takes the key by.
KEY_KEYWORD_OF = {"get_or_create_session": "name"}

#: Records of who did something. The key labels the row and nothing judges it.
RECORDS = frozenset(
    {
        "log_tool_invocation",
        "log_api_access",
        "SecurityEvent",
        "Attribution",
        "AutoSkillProvenance",
        "record_call",
        "record_units",
    }
)

#: A name a key is assigned to, and a function that returns one.
KEY_TARGET = re.compile(r"(^|_)session_key$")
KEY_FUNCTION = re.compile(r"(^|_)session_key(_for)?$|^agent_work_id$|^new_chat_name$|^owned_key$")

#: The shape of a kind's prefix: a word and the separator after it.
PREFIX_SHAPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*[:\-]")

Locals = dict[str, list[ast.expr]]


@dataclass(frozen=True)
class Mint:
    """One place a key is minted: where, and how its prefix is spelled there."""

    where: str
    literal: str | None  # the prefix as spelled there, or None when it is read from a row
    dynamic: bool  # whether more of the key follows the literal (an f-string's or a sum's head)


class _Module:
    """What the census needs of one module: its constants, its imports, and its tree."""

    def __init__(self, name: str, tree: ast.Module) -> None:
        self.name = name
        self.tree = tree
        self.constants: dict[str, ast.expr] = {}
        self.imports: dict[str, tuple[str, str]] = {}  # local name -> (module, attribute)
        self.modules: dict[str, str] = {}  # local name -> module (``import a.b as c``)
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                if isinstance(node.targets[0], ast.Name):
                    self.constants[node.targets[0].id] = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.value is not None:
                    self.constants[node.target.id] = node.value
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    self.imports[alias.asname or alias.name] = (node.module, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.modules[alias.asname] = alias.name


def _assigned(function: ast.AST) -> Locals:
    """What each plain name is assigned anywhere in *function*."""
    names: Locals = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                names.setdefault(node.target.id, []).append(node.value)
    return names


def _callee(call: ast.Call) -> str:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return call.func.id if isinstance(call.func, ast.Name) else ""


class Census:
    """The session keys a set of modules mints, keyed by module name."""

    def __init__(self, sources: dict[str, str]) -> None:
        self.modules = {name: _Module(name, ast.parse(text)) for name, text in sources.items()}

    # ── what a key's prefix is spelled by ────────────────────────────────────────────────
    def _module_named(self, module: _Module, local: str) -> str | None:
        """The module the name *local* stands for in *module*, when it stands for one."""
        if local in module.modules:
            return module.modules[local]
        if local in module.imports:
            dotted = ".".join(module.imports[local])
            if dotted in self.modules or dotted == REGISTRY:
                return dotted
        return None

    def _is_row(self, module: _Module, node: ast.expr) -> bool:
        """Whether *node* is a row of the registry (``session_keys.TILE``, an imported ``TILE``)."""
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return self._module_named(module, node.value.id) == REGISTRY
        if isinstance(node, ast.Name):
            if node.id in module.imports:
                return module.imports[node.id][0] == REGISTRY
            return module.name == REGISTRY and node.id in module.constants
        return False

    def heads(self, module: _Module, node: ast.expr, local: Locals, seen: frozenset = frozenset()):
        """``(literal, dynamic)`` for each way the string *node* can begin; the literal is
        ``None`` when the beginning is read from a row of the registry."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [(node.value, False)]
        if isinstance(node, ast.JoinedStr) and node.values:
            first = node.values[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                return [(first.value, len(node.values) > 1)]
            if isinstance(first, ast.FormattedValue):
                return [(text, True) for text, _ in self.heads(module, first.value, local, seen)]
            return []
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return [(text, True) for text, _ in self.heads(module, node.left, local, seen)]
        if isinstance(node, ast.BoolOp):
            return [h for value in node.values for h in self.heads(module, value, local, seen)]
        if isinstance(node, ast.IfExp):
            return [
                h
                for branch in (node.body, node.orelse)
                for h in self.heads(module, branch, local, seen)
            ]
        if isinstance(node, ast.Call):
            called = node.func
            if isinstance(called, ast.Attribute) and called.attr == "key":
                return [(None, True)] if self._is_row(module, called.value) else []
            return []
        if isinstance(node, ast.Attribute):
            if node.attr == "prefix" and self._is_row(module, node.value):
                return [(None, True)]
            if isinstance(node.value, ast.Name):
                owner = self._module_named(module, node.value.id)
                if owner == REGISTRY:
                    return [(None, False)]
                if owner in self.modules:
                    return self._constant(self.modules[owner], node.attr, seen)
            return []
        if isinstance(node, ast.Name):
            if node.id in local and ("", node.id) not in seen:
                inner = seen | {("", node.id)}
                return [
                    h for value in local[node.id] for h in self.heads(module, value, local, inner)
                ]
            if node.id in module.imports:
                origin, attribute = module.imports[node.id]
                if origin == REGISTRY:
                    return [(None, False)]
                if origin in self.modules:
                    return self._constant(self.modules[origin], attribute, seen)
                return []
            return self._constant(module, node.id, seen)
        return []

    def _constant(self, module: _Module, name: str, seen: frozenset):
        """The beginnings of the module constant *name* of *module*."""
        if (module.name, name) in seen or name not in module.constants:
            return []
        return self.heads(module, module.constants[name], {}, seen | {(module.name, name)})

    # ── where a string becomes a key ─────────────────────────────────────────────────────
    def _handed(self, node: ast.AST):
        """The key expressions the node *node* hands over as a session key."""
        if isinstance(node, ast.Call):
            callee = _callee(node)
            if callee in RECORDS:
                return
            for keyword in node.keywords:
                if keyword.arg in KEY_KEYWORDS or keyword.arg == KEY_KEYWORD_OF.get(callee):
                    yield keyword.value
            index = KEY_POSITIONAL.get(callee)
            if index is not None and len(node.args) > index:
                yield node.args[index]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                named = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
                if named and KEY_TARGET.search(named):
                    yield node.value
        elif isinstance(node, ast.Return) and node.value is not None:
            yield node.value

    def _visit(self, module: _Module, node: ast.AST, local: Locals, returns_key: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                named = getattr(child, "name", "")
                yield from self._visit(
                    module, child, _assigned(child), bool(KEY_FUNCTION.search(named))
                )
                continue
            if not isinstance(child, ast.Return) or returns_key:
                for value in self._handed(child):
                    yield value, local
            yield from self._visit(module, child, local, returns_key)

    def mints(self) -> list[Mint]:
        found: list[Mint] = []
        for module in self.modules.values():
            for value, local in self._visit(module, module.tree, {}, False):
                for literal, dynamic in self.heads(module, value, local):
                    found.append(Mint(f"{module.name}:{value.lineno}", literal, dynamic))
        return found


def _row_name(kind: session_keys.SessionKind) -> str:
    return next(name for name, value in vars(session_keys).items() if value is kind)


def violations(census: Census) -> list[str]:
    """What the census refuses, one sentence each."""
    said: set[str] = set()
    for mint in census.mints():
        if mint.literal is None or mint.where.startswith(f"{REGISTRY}:"):
            continue
        kind = session_keys.kind_of(mint.literal)
        if kind is not None:
            said.add(
                f"{mint.where} spells {kind.prefix!r} where it mints a session key: mint it "
                f"through session_keys.{_row_name(kind)}, so the kind cannot drift from its "
                f"classification"
            )
            continue
        shape = PREFIX_SHAPE.match(mint.literal)
        if shape or mint.dynamic:
            said.add(
                f"{mint.where} mints session keys under "
                f"{(shape.group(0) if shape else mint.literal)!r}, a kind session_keys has no row "
                f"for: add one that says whether anybody watches that work"
            )
    return sorted(said)


def _tree_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        parts = list(path.relative_to(SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        sources[".".join(parts)] = path.read_text(encoding="utf-8")
    return sources


@pytest.fixture(scope="module")
def tree_census() -> Census:
    return Census(_tree_sources())


# ── the rail ──────────────────────────────────────────────────────────────────────────────


def test_every_session_key_the_source_mints_names_a_classified_kind(tree_census):
    found = violations(tree_census)
    assert not found, "\n".join(found)


def test_the_census_sees_the_tile_and_app_mints_read_from_their_rows(tree_census):
    """The vacuity control for the rail above: a census that saw nothing would pass it. The mints
    of the two kinds this table was missing are found, and found reading their rows."""
    by_module: dict[str, list[Mint]] = {}
    for mint in tree_census.mints():
        by_module.setdefault(mint.where.split(":")[0], []).append(mint)
    for module in ("personalclaw.dashboard.tile_refresh", "personalclaw.dashboard.handlers.apps"):
        read = [m for m in by_module.get(module, []) if m.literal is None]
        assert read, f"the census sees no session key {module} mints"


# ── the census refuses what it says it refuses ─────────────────────────────────────────────

_SNIPPET_UNCLASSIFIED = """
def start(x):
    session_key = f"newkind:{x}"
    return session_key
"""

_SNIPPET_SPELLED = """
TILE_PREFIX = "tile:"

def refresh(sessions, ref):
    return sessions.get_or_create(f"{TILE_PREFIX}{ref}")
"""

_SNIPPET_FROM_THE_ROW = """
from personalclaw import session_keys

def refresh(sessions, ref):
    key = session_keys.TILE.key(ref)
    return sessions.get_or_create(key)
"""

_SNIPPET_A_RECORD = """
def audit(sel, x):
    sel.log_tool_invocation(session_key=f"newkind:{x}", tool_name="t")
"""


def test_a_kind_nothing_classifies_is_refused():
    found = violations(Census({"personalclaw.example": _SNIPPET_UNCLASSIFIED}))
    assert found == [
        "personalclaw.example:3 mints session keys under 'newkind:', a kind session_keys has no "
        "row for: add one that says whether anybody watches that work"
    ]


def test_a_rows_prefix_spelled_at_a_mint_site_is_refused_through_its_constant():
    found = violations(Census({"personalclaw.example": _SNIPPET_SPELLED}))
    assert found == [
        "personalclaw.example:5 spells 'tile:' where it mints a session key: mint it through "
        "session_keys.TILE, so the kind cannot drift from its classification"
    ]


def test_a_key_minted_from_its_row_and_a_record_of_who_did_something_pass():
    census = Census(
        {"personalclaw.example": _SNIPPET_FROM_THE_ROW, "personalclaw.audit": _SNIPPET_A_RECORD}
    )
    assert violations(census) == []
    assert [m.literal for m in census.mints() if m.where.startswith("personalclaw.example")] == [
        None
    ]


# ── the table itself ──────────────────────────────────────────────────────────────────────


def test_each_kind_has_one_prefix_ending_in_its_separator_and_says_who_runs():
    prefixes = [kind.prefix for kind in session_keys.KINDS]
    assert len(prefixes) == len(set(prefixes))
    for kind in session_keys.KINDS:
        assert kind.prefix[-1] in ":-_", kind
        assert kind.who and kind.who[0].isupper(), kind
    rows = {v for v in vars(session_keys).values() if isinstance(v, session_keys.SessionKind)}
    assert rows == set(session_keys.KINDS), "a row defined but not in KINDS is never matched"


def test_a_kind_inside_another_is_classified_as_that_one_is():
    """A longer prefix wins the match (``kind_of``), so one nested in another must not flip the
    answer for part of its parent's keys by accident."""
    for inner in session_keys.KINDS:
        for outer in session_keys.KINDS:
            if inner is not outer and inner.prefix.startswith(outer.prefix):
                assert (inner.unattended, inner.stateless) == (outer.unattended, outer.stateless)


@pytest.mark.parametrize("kind", session_keys.KINDS, ids=lambda kind: kind.prefix)
def test_the_policy_reads_each_kind_as_its_row_says(kind):
    key = kind.key("x")
    assert session_keys.kind_of(key) is kind
    assert is_unattended_session(key) is kind.unattended
    assert session_keys.is_stateless(key) is kind.stateless
